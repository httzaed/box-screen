"""Shared LED presets and full 24-bit RGB color support (no hardware imports)."""
import re

COLORS = {
    "red": (255, 0, 0),
    "coral": (255, 80, 60),
    "orange": (255, 40, 0),
    "peach": (255, 160, 100),
    "amber": (255, 130, 0),
    "gold": (255, 175, 0),
    "yellow": (255, 200, 0),
    "lime": (160, 255, 0),
    "green": (0, 255, 100),
    "emerald": (0, 180, 90),
    "mint": (100, 255, 180),
    "teal": (0, 210, 150),
    "cyan": (0, 200, 255),
    "ice": (150, 230, 255),
    "azure": (0, 130, 255),
    "blue": (0, 80, 255),
    "indigo": (75, 0, 255),
    "lavender": (180, 130, 255),
    "violet": (180, 0, 255),
    "magenta": (255, 0, 255),
    "rose": (255, 105, 180),
    "pink": (255, 20, 100),
    "warm_white": (255, 210, 150),
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
