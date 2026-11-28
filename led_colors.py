"""Shared LED presets and full 24-bit RGB color support (no hardware imports).

Valeurs calibrées à l'oeil sur les vraies LEDs (test_led_presets.py, 2026-11-17) :
les LEDs sont linéaires, donc les canaux secondaires sont bien plus bas qu'à
l'écran. Les couleurs écran du dashboard restent dans phosphor_ui.html.
"""
import re

COLORS = {
    "red": (255, 0, 0),
    "coral": (255, 20, 10),
    "orange": (255, 40, 0),
    "peach": (255, 70, 8),
    "amber": (255, 56, 0),
    "gold": (255, 110, 0),
    "yellow": (255, 200, 0),
    "lime": (160, 255, 0),
    "green": (0, 255, 20),
    "emerald": (0, 180, 40),
    "mint": (15, 255, 70),
    "teal": (0, 210, 150),
    "cyan": (0, 255, 110),
    "ice": (0, 255, 200),
    "azure": (0, 200, 255),
    "blue": (0, 80, 255),
    "indigo": (75, 0, 255),
    "lavender": (130, 10, 255),
    "violet": (180, 0, 255),
    "magenta": (255, 0, 255),
    "rose": (255, 45, 110),
    "pink": (255, 20, 100),
    "warm_white": (255, 160, 60),
    "white": (255, 255, 255),
    "silver": (150, 150, 150),
    "black": (0, 0, 0),
}


def is_led_color(value):
    return isinstance(value, str) and (
        value in COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is not None)


def resolve_color(value):
    """Resolve saved preset names or #RRGGBB; retain the legacy fallback."""
    if isinstance(value, str):
        if value in COLORS:
            return COLORS[value]
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    return COLORS["violet"]
