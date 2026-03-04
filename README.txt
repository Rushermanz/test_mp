Bahrain LAN Race - HOW TO USE
==============================

1) What to send to all players
------------------------------
Send the FULL project folder to everyone, including:
- race_bot.py
- utils.py
- requirements.txt
- bahrain_bot1_run.json
- bahrain_bot2_run.json
- bahrain_bot3_run.json
- assets/ (entire folder)

2) Install Python dependency (each player)
-------------------------------------------
Open terminal inside the project folder and run:

pip install -r requirements.txt

(If pip is not available, try: python3 -m pip install -r requirements.txt)

3) Find host LAN IP (host PC)
-----------------------------
Linux/macOS:
- Run: hostname -I

Windows:
- Run: ipconfig
- Use IPv4 address (example: 192.168.1.25)

4) Start the game
-----------------
Host starts server + game:

python race_bot.py --lan host --port 5005 --name Host

Clients join host:

python race_bot.py --lan client --host-ip <HOST_LAN_IP> --port 5005 --name Player2

Example client command:
python race_bot.py --lan client --host-ip 192.168.1.25 --port 5005 --name Ali

5) Lobby / Room controls
------------------------
- All players join the lobby first.
- Host sees connected slots.
- Host presses ENTER to launch race for everyone.
- Clients wait in lobby until host starts.
- ESC exits game.

6) Offline mode (no LAN)
-------------------------
Run:

python race_bot.py --lan off

7) Troubleshooting
------------------
- If client cannot connect:
  - Make sure host and clients are on same Wi-Fi/LAN.
  - Verify host IP is correct.
  - Ensure port 5005 is not blocked by firewall.
- If audio/image errors appear:
  - Confirm assets/ folder is present and complete.
- If 'SERVER FULL' appears:
  - Maximum is 4 players total (1 host + up to 3 clients).
