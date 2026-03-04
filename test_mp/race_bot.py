import argparse
import json
import math
import socket
import sys
import time

import pygame

from utils import scale_image

MAX_PLAYERS = 4
SPAWN_POSITIONS = [
    (4204, 4135),
    (3760, 4028),
    (3915, 4137),
    (4050, 4033),
]
CAR_IMAGES = {
    0: "assets/red_car.png",
    1: "assets/white_car.png",
    2: "assets/purple_car.png",
    3: "assets/grey_car.png",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Bahrain race bot")
    parser.add_argument("--lan", choices=["off", "host", "client"], default="off")
    parser.add_argument("--host-ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--name", default="player")
    return parser.parse_args()


class LanServer:
    def __init__(self, port, host_name):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", port))
        self.sock.setblocking(False)
        self.clients = {}
        self.last_seen = {}
        self.states = {}
        self.player_names = {0: host_name}
        self.race_started = False
        self.race_round = 0

    def _safe_send(self, payload, addr):
        try:
            self.sock.sendto(json.dumps(payload).encode("utf-8"), addr)
        except OSError:
            pass

    def _next_available_id(self):
        used_ids = set(self.clients.values())
        for candidate in range(1, MAX_PLAYERS):
            if candidate not in used_ids:
                return candidate
        return None

    def update(self):
        now = time.time()
        while True:
            try:
                raw, addr = self.sock.recvfrom(4096)
            except BlockingIOError:
                break
            except OSError:
                break

            try:
                msg = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            msg_type = msg.get("type")
            if msg_type == "hello":
                self.last_seen[addr] = now
                requested_name = str(msg.get("name", "player")).strip()[:16] or "player"
                if addr not in self.clients:
                    assigned = self._next_available_id()
                    if assigned is None:
                        self._safe_send({"type": "full"}, addr)
                        continue
                    self.clients[addr] = assigned
                player_id = self.clients[addr]
                self.player_names[player_id] = requested_name
                self._safe_send({"type": "assign", "id": player_id, "max_players": MAX_PLAYERS}, addr)
            elif msg_type == "state":
                player_id = self.clients.get(addr)
                if player_id is None:
                    continue
                self.last_seen[addr] = now
                self.states[player_id] = {
                    "x": float(msg.get("x", SPAWN_POSITIONS[player_id][0])),
                    "y": float(msg.get("y", SPAWN_POSITIONS[player_id][1])),
                    "angle": float(msg.get("angle", 90)),
                    "speed": float(msg.get("speed", 0)),
                    "lap": int(msg.get("lap", 1)),
                }

        stale_addrs = [addr for addr, seen in self.last_seen.items() if now - seen > 5]
        for addr in stale_addrs:
            player_id = self.clients.pop(addr, None)
            self.last_seen.pop(addr, None)
            if player_id is not None:
                self.states.pop(player_id, None)
                self.player_names.pop(player_id, None)

    def set_host_state(self, state):
        self.states[0] = state

    def start_race(self):
        self.race_started = True
        self.race_round += 1

    def reset_to_lobby(self):
        self.race_started = False

    def get_player_names(self):
        return dict(self.player_names)

    def broadcast_snapshot(self):
        payload = {
            "type": "snapshot",
            "players": self.states,
            "race_started": self.race_started,
            "race_round": self.race_round,
            "player_names": self.player_names,
        }
        for addr in list(self.clients.keys()):
            self._safe_send(payload, addr)


class LanClient:
    def __init__(self, host_ip, port, name):
        self.server_addr = (host_ip, port)
        self.name = name
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.player_id = None
        self.remote_states = {}
        self.last_hello = 0
        self.is_full = False
        self.race_started = False
        self.race_round = 0
        self.player_names = {}

    def _safe_send(self, payload):
        try:
            self.sock.sendto(json.dumps(payload).encode("utf-8"), self.server_addr)
        except OSError:
            pass

    def send_hello_if_needed(self):
        now = time.time()
        if self.player_id is None and now - self.last_hello >= 1:
            self._safe_send({"type": "hello", "name": self.name})
            self.last_hello = now

    def send_state(self, state):
        if self.player_id is None:
            return
        payload = {"type": "state", "id": self.player_id, **state}
        self._safe_send(payload)

    def update(self):
        self.send_hello_if_needed()
        while True:
            try:
                raw, _ = self.sock.recvfrom(8192)
            except BlockingIOError:
                break
            except OSError:
                break

            try:
                msg = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            msg_type = msg.get("type")
            if msg_type == "assign":
                self.player_id = int(msg.get("id", 0))
            elif msg_type == "snapshot":
                players = msg.get("players", {})
                parsed = {}
                for key, value in players.items():
                    try:
                        parsed[int(key)] = value
                    except (TypeError, ValueError):
                        continue
                self.remote_states = parsed
                self.race_started = bool(msg.get("race_started", False))
                self.race_round = int(msg.get("race_round", 0))
                names = msg.get("player_names", {})
                parsed_names = {}
                for key, value in names.items():
                    try:
                        parsed_names[int(key)] = str(value)
                    except (TypeError, ValueError):
                        continue
                self.player_names = parsed_names
            elif msg_type == "full":
                self.is_full = True


pygame.mixer.init()
idle_sound = pygame.mixer.Sound("assets/idle.wav")
idle_sound.set_volume(1.0)
idle_sound.play(loops=-1)
accelerate_sound = pygame.mixer.Sound("assets/accelerate.wav")
accelerate_sound.set_volume(0.2)
collision_sound = pygame.mixer.Sound("assets/collision.wav")
drs_sound = pygame.mixer.Sound("assets/drs.wav")


class Car(pygame.sprite.Sprite):
    def __init__(self, pos, group, image_path, angle=90):
        super().__init__(group)
        self.original_image = scale_image(pygame.image.load(image_path).convert_alpha(), 0.9)
        self.image = self.original_image
        self.rect = self.image.get_rect(center=pos)
        self.mask = pygame.mask.from_surface(self.image)
        self.pos = pygame.Vector2(pos)
        self.angle = angle
        self.speed = 0
        self.acceleration = 0.05
        self.max_speed = 8
        self.original_max_speed = self.max_speed
        self.deceleration = 0.06
        self.turn_speed = 1.5
        self.last_pos = self.pos.copy()
        self.drs_active = False
        self.current_wp = 0
        self.lap_count = 1

    def move(self):
        self.last_pos = self.pos.copy()
        rad = math.radians(self.angle)
        direction = pygame.Vector2(math.sin(rad), math.cos(rad)) * -1
        self.pos += direction * self.speed
        self.rect.center = self.pos
        self.image = pygame.transform.rotate(self.original_image, self.angle)
        self.rect = self.image.get_rect(center=self.rect.center)
        self.mask = pygame.mask.from_surface(self.image)

    def rollback(self):
        self.pos = self.last_pos
        self.rect.center = self.pos
        self.speed = 0

    def get_total_progress(self):
        laps_done = getattr(self, "lap_count", 1)
        finish = pygame.Vector2(4657, 4083)
        max_distance = 5000
        lap_progress = 1.0 - (self.pos.distance_to(finish) / max_distance)
        lap_progress = max(0.0, min(1.0, lap_progress))
        return (laps_done - 1) + lap_progress


class PlayerCar(Car):
    def __init__(self, pos, group, image_path):
        super().__init__(pos, group, image_path, angle=90)
        self.image = pygame.transform.rotate(self.original_image, self.angle)
        self.rect = self.image.get_rect(center=self.rect.center)
        self.mask = pygame.mask.from_surface(self.image)

    def update(self):
        keys = pygame.key.get_pressed()
        if not race_started or countdown_timer > 0 or camera_group.show_result:
            return
        if network_mode == "client" and lan_client and lan_client.player_id is None:
            return

        if keys[pygame.K_UP]:
            if self.speed == 0:
                accelerate_sound.play()
            self.speed = min(self.speed + self.acceleration, self.max_speed)
        elif keys[pygame.K_DOWN]:
            self.speed = max(self.speed - self.acceleration, -self.max_speed / 2)
        else:
            if abs(self.speed) < self.deceleration:
                self.speed = 0
            else:
                self.speed += self.deceleration if self.speed < 0 else -self.deceleration

            self.speed = max(min(self.speed, self.max_speed), -self.max_speed / 2)

        if self.speed != 0:
            turn = self.turn_speed * (1 if self.speed > 0 else -1)
            if keys[pygame.K_LEFT]:
                self.angle += turn
            if keys[pygame.K_RIGHT]:
                self.angle -= turn
        self.move()

    def rollback(self):
        super().rollback()
        collision_sound.play()


class GhostBotCar(Car):
    def __init__(self, group, data, image_path, start_pos):
        first = data[0]
        super().__init__(start_pos, group, image_path, angle=90)
        self.data = data
        self.frame = 0
        self.lap_count = 1
        self.total_laps = 3
        self.finished = False
        json_start = pygame.Vector2(first["x"], first["y"])
        self.offset = pygame.Vector2(start_pos) - json_start
        self.image = pygame.transform.rotate(self.original_image, self.angle)
        self.rect = self.image.get_rect(center=self.rect.center)
        self.mask = pygame.mask.from_surface(self.image)

    def update(self):
        if not race_started or countdown_timer > 0 or camera_group.show_result or self.finished:
            return

        if self.frame >= len(self.data):
            self.finished = True
            return

        d = self.data[self.frame]
        replay_pos = pygame.Vector2(d["x"], d["y"]) + self.offset
        self.pos = replay_pos
        self.angle = d["angle"]
        self.speed = d["speed"]

        self.rect.center = self.pos
        self.image = pygame.transform.rotate(self.original_image, self.angle)
        self.rect = self.image.get_rect(center=self.rect.center)
        self.mask = pygame.mask.from_surface(self.image)
        self.frame += 1

        foff = (
            int(self.rect.left - camera_group.finish_rect.left),
            int(self.rect.top - camera_group.finish_rect.top),
        )
        crossed = camera_group.finish_mask.overlap(self.mask, foff)
        if crossed and not getattr(self, "last_cross", False):
            self.lap_count += 1
        self.last_cross = bool(crossed)

    def get_total_progress(self):
        lap_progress = self.frame / len(self.data)
        return (self.lap_count - 1) + lap_progress


class NetworkCar(Car):
    def __init__(self, pos, group, image_path):
        super().__init__(pos, group, image_path, angle=90)
        self.image = pygame.transform.rotate(self.original_image, self.angle)
        self.rect = self.image.get_rect(center=self.rect.center)
        self.mask = pygame.mask.from_surface(self.image)

    def update_from_state(self, state):
        self.pos = pygame.Vector2(float(state.get("x", self.pos.x)), float(state.get("y", self.pos.y)))
        self.angle = float(state.get("angle", self.angle))
        self.speed = float(state.get("speed", self.speed))
        self.lap_count = int(state.get("lap", self.lap_count))
        self.rect.center = self.pos
        self.image = pygame.transform.rotate(self.original_image, self.angle)
        self.rect = self.image.get_rect(center=self.rect.center)
        self.mask = pygame.mask.from_surface(self.image)

    def update(self):
        return


with open("bahrain_bot1_run.json") as f1, open("bahrain_bot2_run.json") as f2, open("bahrain_bot3_run.json") as f3:
    ghost_data1 = json.load(f1)
    ghost_data2 = json.load(f2)
    ghost_data3 = json.load(f3)


def get_race_positions(cars):
    return sorted(cars, key=lambda c: c.get_total_progress(), reverse=True)


class CameraGroup(pygame.sprite.Group):
    def __init__(self):
        super().__init__()
        self.display_surface = pygame.display.get_surface()
        self.offset = pygame.Vector2()
        self.track_surface = scale_image(pygame.image.load("assets/bahrain.png").convert_alpha(), 2.7)
        self.border_surface = scale_image(pygame.image.load("assets/bahrain_border.png").convert_alpha(), 2.7)
        self.drs_surface = scale_image(pygame.image.load("assets/bahrain_drs.png").convert_alpha(), 2.7)
        self.finish_img = pygame.transform.rotate(scale_image(pygame.image.load("assets/finish.png").convert_alpha(), 1.9), 90)
        self.border_mask = pygame.mask.from_surface(self.border_surface)
        self.drs_mask = pygame.mask.from_surface(self.drs_surface)
        self.finish_mask = pygame.mask.from_surface(self.finish_img)
        self.track_rect = self.track_surface.get_rect(topleft=(0, 0))
        self.border_rect = self.border_surface.get_rect(topleft=(0, 0))
        self.drs_rect = self.drs_surface.get_rect(topleft=(0, 0))
        self.finish_rect = self.finish_img.get_rect(center=(4657, 4083))
        self.half_w = self.display_surface.get_width() // 2
        self.half_h = self.display_surface.get_height() // 2
        self.minimap_scale = 0.025
        self.minimap = pygame.transform.scale(
            self.track_surface,
            (
                int(self.track_surface.get_width() * self.minimap_scale),
                int(self.track_surface.get_height() * self.minimap_scale),
            ),
        )
        self.minimap_rect = self.minimap.get_rect(topleft=(20, 70))
        self.start_time = 0
        self.lap_started = False
        self.crossed_once = False
        self.recently_crossed = False
        self.current_lap = 1
        self.total_laps = 3
        self.lap_times = []
        self.show_result = False

    def center_target_camera(self, target):
        self.offset.x = target.rect.centerx - self.half_w
        self.offset.y = target.rect.centery - self.half_h

    def custom_draw(self, player, opponents):
        self.center_target_camera(player)
        self.display_surface.fill((0, 100, 0))
        self.display_surface.blit(self.track_surface, self.track_rect.topleft - self.offset)
        self.display_surface.blit(self.border_surface, self.border_rect.topleft - self.offset)

        for spr in sorted(self.sprites(), key=lambda s: s.rect.centery):
            self.display_surface.blit(spr.image, spr.rect.topleft - self.offset)

        player_off = (
            int(player.rect.left - self.border_rect.left),
            int(player.rect.top - self.border_rect.top),
        )
        if self.border_mask.overlap(player.mask, player_off):
            player.rollback()

        doff = (int(player.rect.left - self.drs_rect.left), int(player.rect.top - self.drs_rect.top))
        in_drs = bool(self.drs_mask.overlap(player.mask, doff))
        if in_drs and not player.drs_active:
            drs_sound.play()
        player.drs_active = in_drs
        player.max_speed = player.original_max_speed + 2 if in_drs else player.original_max_speed

        foff = (int(player.rect.left - self.finish_rect.left), int(player.rect.top - self.finish_rect.top))
        if self.finish_mask.overlap(player.mask, foff):
            if not self.recently_crossed:
                if not self.crossed_once:
                    self.crossed_once = True
                elif self.lap_started:
                    duration = time.time() - self.start_time
                    self.lap_times.append(round(duration, 2))
                    self.current_lap += 1
                    if self.current_lap > self.total_laps:
                        self.lap_started = False
                        self.show_result = True
                    else:
                        self.start_time = time.time()
        self.recently_crossed = bool(self.finish_mask.overlap(player.mask, foff))
        player.lap_count = self.current_lap

        self.display_surface.blit(self.finish_img, self.finish_rect.topleft - self.offset)

        timer = f"Time: {time.time() - self.start_time:.2f}s" if self.lap_started else "--.--s"
        self.display_surface.blit(font.render(timer, True, (255, 255, 255)), (20, 20))
        self.display_surface.blit(font.render(f"Lap: {self.current_lap}/{self.total_laps}", True, (255, 255, 255)), (WIDTH - 220, 60))
        self.display_surface.blit(font.render(f"Speed: {player.speed:.1f} px/frame", True, (255, 255, 255)), (20, HEIGHT - 40))
        if in_drs:
            self.display_surface.blit(font.render("DRS ACTIVE", True, (0, 255, 0)), (WIDTH - 180, 20))

        pygame.draw.rect(self.display_surface, (255, 255, 255), self.minimap_rect.inflate(4, 4), 2)
        self.display_surface.blit(self.minimap, self.minimap_rect)

        mx = int(player.rect.centerx * self.minimap_scale)
        my = int(player.rect.centery * self.minimap_scale)
        pygame.draw.circle(
            self.display_surface,
            (255, 0, 0),
            (self.minimap_rect.left + mx, self.minimap_rect.top + my),
            3,
        )

        all_cars = [player] + opponents
        pos = get_race_positions(all_cars).index(player) + 1
        self.display_surface.blit(font.render(f"Position: {pos}/{len(all_cars)}", True, (255, 255, 0)), (WIDTH - 220, HEIGHT - 40))

        if race_started and countdown_timer > 0:
            cd = font.render(f"{int(countdown_timer) + 1}", True, (255, 0, 0))
            self.display_surface.blit(cd, (WIDTH // 2 - 20, HEIGHT // 2 - 20))
        elif race_started and show_go and not self.show_result:
            go = font.render("GO!", True, (0, 255, 0))
            self.display_surface.blit(go, (WIDTH // 2 - go.get_width() // 2, HEIGHT // 2 - 50))

        if self.show_result:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            self.display_surface.blit(overlay, (0, 0))
            self.display_surface.blit(font.render("RACE COMPLETE!", True, (255, 255, 255)), (WIDTH // 2 - 100, 80))
            for i, lap_time in enumerate(self.lap_times):
                self.display_surface.blit(
                    font.render(f"Lap {i + 1}: {lap_time:.2f}s", True, (200, 200, 200)),
                    (WIDTH // 2 - 80, 140 + i * 40),
                )
            self.display_surface.blit(
                font.render("Press ENTER to retry or ESC to exit", True, (255, 255, 0)),
                (WIDTH // 2 - 170, HEIGHT - 100),
            )


args = parse_args()
network_mode = args.lan
lan_server = LanServer(args.port, args.name) if network_mode == "host" else None
lan_client = LanClient(args.host_ip, args.port, args.name) if network_mode == "client" else None

pygame.init()
pygame.mixer.music.load("assets/easy.mp3")
pygame.mixer.music.set_volume(0.5)
pygame.mixer.music.play(-1)

WIDTH, HEIGHT = pygame.display.Info().current_w, pygame.display.Info().current_h
screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
clock = pygame.time.Clock()
font = pygame.font.Font(None, 36)
start_ticks = pygame.time.get_ticks()
countdown_timer = 0
show_go = False
race_started = network_mode == "off"

camera_group = CameraGroup()

local_id = 0
local_car_image = CAR_IMAGES[0]
if network_mode == "client":
    local_car_image = CAR_IMAGES[0]

player = PlayerCar(SPAWN_POSITIONS[0], camera_group, local_car_image)
opponents = []
opponents_by_id = {}

if network_mode == "off":
    ghost_bot1 = GhostBotCar(camera_group, ghost_data1, "assets/white_car.png", start_pos=SPAWN_POSITIONS[1])
    ghost_bot2 = GhostBotCar(camera_group, ghost_data2, "assets/purple_car.png", start_pos=SPAWN_POSITIONS[2])
    ghost_bot3 = GhostBotCar(camera_group, ghost_data3, "assets/grey_car.png", start_pos=SPAWN_POSITIONS[3])
    opponents = [ghost_bot1, ghost_bot2, ghost_bot3]
    camera_group.add(player, *opponents)
else:
    camera_group.add(player)

assignment_applied = False


def current_local_state():
    return {
        "x": player.pos.x,
        "y": player.pos.y,
        "angle": player.angle,
        "speed": player.speed,
        "lap": player.lap_count,
    }


def reset_local_race_state(reset_offline_opponents=True):
    player.pos = pygame.Vector2(SPAWN_POSITIONS[local_id])
    player.rect.center = SPAWN_POSITIONS[local_id]
    player.angle = 90
    player.speed = 0
    player.lap_count = 1
    player.last_pos = player.pos.copy()

    if network_mode == "off" and reset_offline_opponents:
        reset_positions = SPAWN_POSITIONS[1:]
        for bot, pos in zip(opponents, reset_positions):
            bot.pos = pygame.Vector2(pos)
            bot.rect.center = pos
            bot.angle = 90
            bot.speed = 0
            bot.frame = 0
            bot.lap_count = 1
            bot.finished = False

    camera_group.current_lap = 1
    camera_group.lap_times = []
    camera_group.lap_started = False
    camera_group.crossed_once = False
    camera_group.recently_crossed = False
    camera_group.show_result = False


def draw_lobby_overlay():
    if network_mode == "off" or race_started or camera_group.show_result:
        return

    if network_mode == "host":
        names = lan_server.get_player_names()
    else:
        names = dict(lan_client.player_names)
        if lan_client.player_id is not None:
            names.setdefault(lan_client.player_id, args.name)

    panel = pygame.Surface((600, 300), pygame.SRCALPHA)
    panel.fill((0, 0, 0, 170))
    panel_rect = panel.get_rect(center=(WIDTH // 2, HEIGHT // 2))
    screen.blit(panel, panel_rect)

    title = font.render("LAN LOBBY", True, (255, 255, 255))
    screen.blit(title, (panel_rect.left + 220, panel_rect.top + 20))

    for slot in range(MAX_PLAYERS):
        name = names.get(slot)
        if name:
            line = f"Slot {slot + 1}: {name}"
            color = (120, 255, 120)
        else:
            line = f"Slot {slot + 1}: waiting..."
            color = (200, 200, 200)
        txt = font.render(line, True, color)
        screen.blit(txt, (panel_rect.left + 40, panel_rect.top + 70 + slot * 45))

    if network_mode == "host":
        hint = "Press ENTER to launch race"
    else:
        hint = "Waiting for host to launch"
    screen.blit(font.render(hint, True, (255, 255, 0)), (panel_rect.left + 140, panel_rect.bottom - 45))


def sync_network_opponents(local_player_id, states):
    active_remote_ids = set()
    for player_id, state in states.items():
        if player_id == local_player_id:
            continue
        if not 0 <= player_id < MAX_PLAYERS:
            continue
        active_remote_ids.add(player_id)
        if player_id not in opponents_by_id:
            car = NetworkCar(SPAWN_POSITIONS[player_id], camera_group, CAR_IMAGES[player_id])
            opponents_by_id[player_id] = car
            opponents.append(car)
        opponents_by_id[player_id].update_from_state(state)

    for player_id in list(opponents_by_id.keys()):
        if player_id not in active_remote_ids:
            car = opponents_by_id.pop(player_id)
            if car in opponents:
                opponents.remove(car)
            car.kill()


while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
            pygame.quit()
            sys.exit()

        if e.type == pygame.KEYDOWN and e.key == pygame.K_RETURN:
            if camera_group.show_result:
                if network_mode == "off":
                    reset_local_race_state()
                    race_started = True
                    start_ticks = pygame.time.get_ticks()
                elif network_mode == "host":
                    reset_local_race_state(reset_offline_opponents=False)
                    race_started = False
                    lan_server.reset_to_lobby()
            elif network_mode == "host" and not race_started:
                reset_local_race_state(reset_offline_opponents=False)
                race_started = True
                lan_server.start_race()
                start_ticks = pygame.time.get_ticks()

    if race_started:
        elapsed = (pygame.time.get_ticks() - start_ticks) / 1000
        countdown_timer = max(0, 3 - elapsed)
        show_go = 0 < elapsed - 3 < 1
    else:
        countdown_timer = 0
        show_go = False

    if race_started and countdown_timer <= 0 and not camera_group.lap_started and not camera_group.show_result:
        camera_group.start_time = time.time()
        camera_group.lap_started = True
        camera_group.crossed_once = True

    if network_mode == "host":
        lan_server.update()
        race_started = lan_server.race_started
        lan_server.set_host_state(current_local_state())
        sync_network_opponents(0, lan_server.states)
        lan_server.broadcast_snapshot()
    elif network_mode == "client":
        lan_client.update()
        if lan_client.player_id is not None and not assignment_applied:
            local_id = lan_client.player_id
            player.original_image = scale_image(pygame.image.load(CAR_IMAGES[local_id]).convert_alpha(), 0.9)
            player.image = pygame.transform.rotate(player.original_image, player.angle)
            player.rect = player.image.get_rect(center=SPAWN_POSITIONS[local_id])
            player.mask = pygame.mask.from_surface(player.image)
            player.pos = pygame.Vector2(SPAWN_POSITIONS[local_id])
            assignment_applied = True

        if lan_client.race_started and not race_started:
            reset_local_race_state(reset_offline_opponents=False)
            start_ticks = pygame.time.get_ticks()
        elif not lan_client.race_started and race_started:
            reset_local_race_state(reset_offline_opponents=False)

        race_started = lan_client.race_started
        sync_network_opponents(local_id, lan_client.remote_states)
        lan_client.send_state(current_local_state())

    camera_group.update()
    camera_group.custom_draw(player, opponents)
    draw_lobby_overlay()

    if network_mode == "host":
        role_text = "LAN HOST (max 4 players)"
    elif network_mode == "client":
        if lan_client.is_full:
            role_text = "SERVER FULL"
        elif lan_client.player_id is None:
            role_text = "CONNECTING TO HOST..."
        else:
            role_text = f"LAN CLIENT - SLOT {lan_client.player_id + 1}/{MAX_PLAYERS}"
    else:
        role_text = "OFFLINE"

    screen.blit(font.render(role_text, True, (255, 255, 255)), (20, 55))

    pygame.display.update()
    clock.tick(60)
