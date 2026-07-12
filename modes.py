"""
modes.py — Unified mode manager for Box Screen

Single source of truth for all mode definitions.
Simplified to 4 essential mode combinations for auto-mode.
"""

from typing import Literal, Tuple
from dataclasses import dataclass

# ── Mode Definitions ───────────────────────────────────────────────────────────────

# Display modes (what's shown on screen)
DisplayMode = Literal["ascii_vhs", "video", "image", "audio", "blank"]
DISPLAY_MODES: list[DisplayMode] = ["ascii_vhs", "video", "image", "audio", "blank"]

# HUD styles (overlay/on-screen display style)
HUDStyle = Literal["full", "terminal", "clock", "split", "tiles", "matrix", "lyrics", "audio_viz", "blank"]
HUD_STYLES: list[HUDStyle] = ["full", "terminal", "clock", "split", "tiles", "matrix", "lyrics", "audio_viz", "blank"]

# LED animation modes
LEDMode = Literal["off", "static", "wave", "beat", "beat_pulse", "gradient", "rainbow", "comet", "breathe", "spin", "dual"]
LED_MODES: list[LEDMode] = ["off", "static", "wave", "beat", "beat_pulse", "gradient", "rainbow", "comet", "breathe", "spin", "dual"]

# ── Mode Combinations (All original combos restored) ───────────────────────────────

ModeKey = Literal["video", "audio", "idle", "manual", "video+lyrics", "image", "image+lyrics", "lyrics", "audio_viz", "ascii_hud", "lyrics_cascade"]


@dataclass
class LEDConfig:
    """Complete LED configuration for case, fans, and keyboard."""
    # Case LEDs
    case_mode: LEDMode = "static"
    case_color: str = "green"
    case_color2: str = "red"
    case_speed: float = 4.0
    case_length: float = 1.0

    # Fan LEDs
    fan_mode: LEDMode = "beat_pulse"
    fan_color: str = "orange"
    fan_color2: str = "orange"
    fan_speed: float = 4.0

    # Keyboard LEDs
    keyboard_mode: LEDMode = "sync_fans"
    keyboard_color: str = "violet"
    keyboard_color2: str = "cyan"
    keyboard_speed: float = 1.0
    keyboard_enabled: bool = True

    # Global settings
    brightness: float = 0.5
    case_decay: float = 2.5


@dataclass
class ModeCombo:
    """A complete mode configuration with display, HUD, and LED settings."""
    key: ModeKey
    display: DisplayMode
    hud: HUDStyle
    led: LEDConfig | None = None
    description: str = ""

    def __post_init__(self):
        if self.led is None:
            self.led = LEDConfig()


# All mode combinations (original + new unified system)
MODE_COMBOS: dict[ModeKey, ModeCombo] = {
    # Original combinations restored
    "video+lyrics": ModeCombo(
        key="video+lyrics",
        display="video",
        hud="lyrics",
        led=LEDConfig(
            case_mode="static",
            case_color="green",
            case_color2="red",
            case_speed=4.0,
            fan_mode="beat_pulse",
            fan_color="orange",
            fan_color2="orange",
            fan_speed=4.0,
            keyboard_mode="sync_fans",
            keyboard_color="violet",
            keyboard_color2="cyan",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.5,
            case_decay=2.5
        ),
        description="Video with lyrics overlay"
    ),
    "video": ModeCombo(
        key="video",
        display="video",
        hud="full",
        led=LEDConfig(
            case_mode="static",
            case_color="green",
            case_color2="red",
            case_speed=4.0,
            fan_mode="beat_pulse",
            fan_color="orange",
            fan_color2="orange",
            fan_speed=4.0,
            keyboard_mode="sync_fans",
            keyboard_color="violet",
            keyboard_color2="cyan",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.5,
            case_decay=2.5
        ),
        description="Video playback with full HUD"
    ),
    "image+lyrics": ModeCombo(
        key="image+lyrics",
        display="image",
        hud="lyrics",
        led=LEDConfig(
            case_mode="static",
            case_color="green",
            case_color2="red",
            case_speed=4.0,
            fan_mode="beat_pulse",
            fan_color="orange",
            fan_color2="orange",
            fan_speed=4.0,
            keyboard_mode="sync_fans",
            keyboard_color="violet",
            keyboard_color2="cyan",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.5,
            case_decay=2.5
        ),
        description="Image slideshow with lyrics"
    ),
    "image": ModeCombo(
        key="image",
        display="image",
        hud="full",
        led=LEDConfig(
            case_mode="static",
            case_color="green",
            case_color2="red",
            case_speed=4.0,
            fan_mode="beat",
            fan_color="orange",
            fan_color2="orange",
            fan_speed=4.0,
            keyboard_mode="sync_fans",
            keyboard_color="violet",
            keyboard_color2="cyan",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.5,
            case_decay=2.5
        ),
        description="Image slideshow with full HUD"
    ),
    "lyrics_cascade": ModeCombo(
        key="lyrics_cascade",
        display="audio",
        hud="lyrics",
        led=LEDConfig(
            case_mode="static",
            case_color="green",
            case_color2="red",
            case_speed=4.0,
            fan_mode="beat_pulse",
            fan_color="orange",
            fan_color2="orange",
            fan_speed=4.0,
            keyboard_mode="sync_fans",
            keyboard_color="violet",
            keyboard_color2="cyan",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.5,
            case_decay=2.5
        ),
        description="Intelligent cascade: lyrics -> audio_viz -> terminal"
    ),
    "lyrics": ModeCombo(
        key="lyrics",
        display="blank",
        hud="lyrics",
        led=LEDConfig(
            case_mode="static",
            case_color="green",
            case_color2="red",
            case_speed=4.0,
            fan_mode="beat_pulse",
            fan_color="orange",
            fan_color2="orange",
            fan_speed=4.0,
            keyboard_mode="sync_fans",
            keyboard_color="violet",
            keyboard_color2="cyan",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.5,
            case_decay=2.5
        ),
        description="Lyrics display on blank background"
    ),
    "audio_viz": ModeCombo(
        key="audio_viz",
        display="blank",
        hud="audio_viz",
        led=LEDConfig(
            case_mode="beat",
            case_color="cyan",
            case_color2="blue",
            case_speed=5.0,
            fan_mode="spin",
            fan_color="pink",
            fan_color2="purple",
            fan_speed=4.0,
            keyboard_mode="beat_pulse_dual",
            keyboard_color="cyan",
            keyboard_color2="purple",
            keyboard_speed=1.5,
            keyboard_enabled=True,
            brightness=0.7,
            case_decay=1.5
        ),
        description="Audio visualization on blank background"
    ),
    "ascii_hud": ModeCombo(
        key="ascii_hud",
        display="ascii_vhs",
        hud="terminal",
        led=LEDConfig(
            case_mode="beat",
            case_color="#2bff88",
            case_color2="#00ff66",
            case_speed=4.0,
            fan_mode="spin",
            fan_color="#2bff88",
            fan_color2="#00ff66",
            fan_speed=3.0,
            keyboard_mode="sync_fans",
            keyboard_color="#2bff88",
            keyboard_color2="#00ff66",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.6,
            case_decay=2.0
        ),
        description="ASCII art with terminal HUD"
    ),
    "blank": ModeCombo(
        key="blank",
        display="blank",
        hud="blank",
        led=LEDConfig(
            case_mode="off",
            case_color="blue",
            case_color2="blue",
            case_speed=2.0,
            fan_mode="off",
            fan_color="blue",
            fan_color2="blue",
            fan_speed=2.0,
            keyboard_mode="off",
            keyboard_color="blue",
            keyboard_color2="blue",
            keyboard_speed=1.0,
            keyboard_enabled=False,
            brightness=0.2,
            case_decay=3.0
        ),
        description="Everything off - blank screen"
    ),
    # New unified modes (for simplicity)
    "audio": ModeCombo(
        key="audio",
        display="audio",
        hud="audio_viz",
        led=LEDConfig(
            case_mode="beat",
            case_color="cyan",
            case_color2="blue",
            case_speed=5.0,
            fan_mode="spin",
            fan_color="pink",
            fan_color2="purple",
            fan_speed=4.0,
            keyboard_mode="beat_pulse_dual",
            keyboard_color="cyan",
            keyboard_color2="purple",
            keyboard_speed=1.5,
            keyboard_enabled=True,
            brightness=0.7,
            case_decay=1.5
        ),
        description="Audio reactive visualizer (simplified)"
    ),
    "idle": ModeCombo(
        key="idle",
        display="blank",
        hud="terminal",
        led=LEDConfig(
            case_mode="off",
            case_color="blue",
            case_color2="blue",
            case_speed=2.0,
            fan_mode="off",
            fan_color="blue",
            fan_color2="blue",
            fan_speed=2.0,
            keyboard_mode="off",
            keyboard_color="blue",
            keyboard_color2="blue",
            keyboard_speed=1.0,
            keyboard_enabled=False,
            brightness=0.2,
            case_decay=3.0
        ),
        description="Low-power idle state"
    ),
    "manual": ModeCombo(
        key="manual",
        display="ascii_vhs",
        hud="terminal",
        led=LEDConfig(
            case_mode="beat",
            case_color="#2bff88",
            case_color2="#00ff66",
            case_speed=4.0,
            fan_mode="spin",
            fan_color="#2bff88",
            fan_color2="#00ff66",
            fan_speed=3.0,
            keyboard_mode="sync_fans",
            keyboard_color="#2bff88",
            keyboard_color2="#00ff66",
            keyboard_speed=1.0,
            keyboard_enabled=True,
            brightness=0.6,
            case_decay=2.0
        ),
        description="Manual override - retro ASCII"
    ),
}

# ── Helper Functions ────────────────────────────────────────────────────────────────

def get_mode(key: ModeKey | str) -> ModeCombo | None:
    """Get a mode combo by key."""
    return MODE_COMBOS.get(key)  # type: ignore


def get_all_modes() -> list[ModeCombo]:
    """Get all mode combos as a list."""
    return list(MODE_COMBOS.values())


def get_mode_keys() -> list[ModeKey]:
    """Get all mode keys."""
    return list(MODE_COMBOS.keys())  # type: ignore


def is_valid_display_mode(mode: str) -> bool:
    """Check if a string is a valid display mode."""
    return mode in DISPLAY_MODES


def is_valid_hud_style(style: str) -> bool:
    """Check if a string is a valid HUD style."""
    return style in HUD_STYLES


def is_valid_led_mode(mode: str) -> bool:
    """Check if a string is a valid LED mode."""
    return mode in LED_MODES


# ── Mode Detection (Context) ───────────────────────────────────────────────────────────


@dataclass
class ModeContext:
    """Context information for automatic mode selection."""
    has_video: bool = False
    has_audio: bool = False
    has_lyrics: bool = False
    audio_level: float = 0.0
    bpm: float | None = None
    is_night: bool = False
    silence_duration: float = 0.0
    video_duration: float | None = None


def recommend_mode(context: ModeContext) -> ModeKey:
    """
    Recommend the best mode based on context.
    This is a simplified version of the auto-mode logic.
    """
    # Manual override takes priority (checked elsewhere)
    # Video with content?
    if context.has_video and context.video_duration and context.video_duration > 10:
        return "video"

    # Audio with lyrics?
    if context.has_audio and context.has_lyrics and context.audio_level > 0.1:
        return "video"  # Video mode handles lyrics

    # Audio only?
    if context.has_audio and context.audio_level > 0.1:
        return "audio"

    # Nothing happening? Go idle
    if context.silence_duration > 8.0:
        return "idle"

    # Default to audio (will show visualizations)
    return "audio"


# ── LED Color Presets ───────────────────────────────────────────────────────────────

# Colors that work well with phosphor theme
LED_COLORS = {
    "phosphor": "#2bff88",      # Primary phosphor green
    "cyan": "#00ffff",
    "blue": "#0066ff",
    "purple": "#9933ff",
    "pink": "#ff6699",
    "red": "#ff3366",
    "orange": "#ff9933",
    "yellow": "#ffcc00",
    "green": "#00ff66",
    "white": "#ffffff",
}

# Default LED color for each mode
MODE_COLORS = {
    "video": "orange",     # Warm for movies
    "audio": "cyan",       # Cool for music
    "idle": "blue",        # Calm for idle
    "manual": "phosphor",  # Classic terminal green
}


def get_led_config(mode: ModeKey) -> LEDConfig:
    """Get the full LED configuration for a mode."""
    mode_combo = MODE_COMBOS.get(mode)
    if mode_combo and mode_combo.led:
        return mode_combo.led
    return LEDConfig()  # Default config


def apply_led_config(led_config: LEDConfig, led_system=None) -> bool:
    """
    Apply LED configuration to the actual LED system.
    Pass the led_fans module instance to control real LEDs.
    Returns True if successful.
    """
    if led_system is None:
        return False

    try:
        # Apply case settings
        led_system.set_cfg(
            case_mode=led_config.case_mode,
            case_color=led_config.case_color,
            case_color2=led_config.case_color2,
            case_speed=led_config.case_speed,
            case_length=led_config.case_length,
            case_decay=led_config.case_decay,
        )

        # Apply fan settings
        led_system.set_cfg(
            fan_mode=led_config.fan_mode,
            fan_color=led_config.fan_color,
            fan_color2=led_config.fan_color2,
            fan_speed=led_config.fan_speed,
        )

        # Apply keyboard settings
        led_system.set_cfg(
            keyboard_mode=led_config.keyboard_mode,
            keyboard_color=led_config.keyboard_color,
            keyboard_color2=led_config.keyboard_color2,
            keyboard_speed=led_config.keyboard_speed,
            keyboard_enabled=led_config.keyboard_enabled,
        )

        # Apply global brightness
        led_system.set_cfg(brightness=led_config.brightness)

        return True
    except Exception as e:
        print(f"[MODES] Error applying LED config: {e}")
        return False


def get_led_color(mode: ModeKey) -> str:
    """Get the recommended primary LED color for a mode (legacy)."""
    config = get_led_config(mode)
    return config.case_color


# ── Scenes (Quick presets) ───────────────────────────────────────────────────────────

SceneKey = Literal["chill", "focus", "party", "night", "stealth"]


@dataclass
class Scene:
    """A preset scene with custom LED settings."""
    key: SceneKey
    mode: ModeKey
    led: LEDConfig
    description: str


SCENES: dict[SceneKey, Scene] = {
    "chill": Scene(
        "chill",
        "audio",
        LEDConfig(
            case_mode="breathe",
            case_color="cyan",
            case_color2="blue",
            case_speed=3.0,
            fan_mode="static",
            fan_color="cyan",
            fan_color2="blue",
            fan_speed=3.0,
            keyboard_mode="sync_fans",
            keyboard_color="cyan",
            keyboard_color2="blue",
            brightness=0.4,
            keyboard_enabled=True,
        ),
        "Relaxed audio visualization with breathing case"
    ),
    "focus": Scene(
        "focus",
        "idle",
        LEDConfig(
            case_mode="static",
            case_color="green",
            case_color2="green",
            case_speed=2.0,
            fan_mode="off",
            fan_color="green",
            fan_color2="green",
            keyboard_mode="static",
            keyboard_color="green",
            keyboard_color2="green",
            brightness=0.3,
            keyboard_enabled=False,
        ),
        "Minimal distraction, calm green"
    ),
    "party": Scene(
        "party",
        "audio",
        LEDConfig(
            case_mode="rainbow",
            case_color="pink",
            case_color2="purple",
            case_speed=6.0,
            fan_mode="beat_pulse_dual",
            fan_color="pink",
            fan_color2="purple",
            fan_speed=5.0,
            keyboard_mode="beat_pulse_dual",
            keyboard_color="pink",
            keyboard_color2="purple",
            brightness=0.9,
            keyboard_enabled=True,
        ),
        "High-energy visualizations with rainbow effects"
    ),
    "night": Scene(
        "night",
        "idle",
        LEDConfig(
            case_mode="wave",
            case_color="blue",
            case_color2="purple",
            case_speed=2.0,
            fan_mode="off",
            fan_color="blue",
            fan_color2="blue",
            keyboard_mode="off",
            keyboard_color="blue",
            keyboard_color2="blue",
            brightness=0.15,
            keyboard_enabled=False,
        ),
        "Late-night dim mode"
    ),
    "stealth": Scene(
        "stealth",
        "idle",
        LEDConfig(
            case_mode="off",
            case_color="off",
            case_color2="off",
            case_speed=1.0,
            fan_mode="off",
            fan_color="off",
            fan_color2="off",
            keyboard_mode="off",
            keyboard_color="off",
            keyboard_color2="off",
            brightness=0.0,
            keyboard_enabled=False,
        ),
        "Everything off - stealth mode"
    ),
}

# ── Export for backward compatibility ─────────────────────────────────────────────────

# Old code references these - keep for compatibility
MODES = DISPLAY_MODES
CASE_MODES = LED_MODES
FAN_MODES = LED_MODES

if __name__ == "__main__":
    # Test mode detection
    ctx = ModeContext(
        has_video=True,
        has_audio=True,
        has_lyrics=True,
        audio_level=0.5,
        bpm=120,
        video_duration=180
    )
    print(f"Recommended mode: {recommend_mode(ctx)}")

    # List all modes
    print("\nAvailable modes:")
    for mode in get_all_modes():
        print(f"  {mode.key}: {mode.description}")
