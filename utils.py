import pygame

def scale_image(image, factor):
    size = round(image.get_width() * factor), round(image.get_height() * factor)
    return pygame.transform.scale(image, size)