"""
dashboard_app.py — Box Screen desktop controller (Dear PyGui)
  pip install dearpygui

Peut tourner de deux façons :
  1. Standalone : python dashboard_app.py
     → communique avec launcher.py via l'API HTTP (localhost:7420)
  2. Embarqué   : import dashboard_app; dashboard_app.run_embedded(switch_fn, switch_hud_fn, get_state_fn)
     → appelle directement les fonctions du launcher, pas besoin du serveur HTTP
"""

import threading
import time
import sys

import dearpygui.dearpygui as dpg

# ── Palette ────────────────────────────────────────────────────────────────────
_BG      = (6,   4,  16, 255)
_PANEL   = (13,  10,  30, 255)
_ACCENT  = (200,  64, 255, 255)
_GREEN   = (0,  255, 157, 255)
_BLUE    = (56, 191, 255, 255)
_ORANGE  = (255, 106,   0, 255)
_MUTED   = (107,  94, 138, 255)
_TEXT    = (212, 200, 240, 255)
_BORDER  = (42,  31,  80, 255)

# Import unified mode system
from modes import (
    DISPLAY_MODES as _MODES,
    HUD_STYLES as _HUD_STYLES,
    LED_MODES as _ALL_LED_MODES,
    LED_COLORS
)

# Simplified LED modes for dashboard UI (must match modes.py LED_MODES)
_CASE_MODES  = ["off", "wave", "beat"]
_FAN_MODES   = ["off", "spin", "static", "beat_pulse"]
_LED_COLORS  = ["violet", "cyan", "blue", "teal", "green", "yellow", "orange", "red", "pink", "white"]
_LED_PREVIEWS = {
    "violet": (180,   0, 255, 255),
    "cyan":   (  0, 200, 255, 255),
    "blue":   (  0,  80, 255, 255),
    "teal":   (  0, 210, 150, 255),
    "green":  (  0, 255, 100, 255),
    "yellow": (255, 200,   0, 255),
    "orange": (255,  85,   0, 255),
    "red":    (255,   0,   0, 255),
    "pink":   (255,  20, 100, 255),
    "white":  (220, 220, 220, 255),
}

# ── Mode de communication ──────────────────────────────────────────────────────
_switch_fn      = None
_switch_hud_fn  = None
_get_state_fn   = None
_set_led_fn     = None   # (zone, val) → None
_HTTP_BASE      = "http://localhost:7420"

_state = {
    "mode":     "ascii_vhs",
    "hud":      "full",
    "bpm":      None,
    "level":    0.0,
    "beat_seq": 0,
    "led": {"case_mode": "beat", "fan_mode": "spin_bpm",
            "case_color": "violet", "fan_color": "violet"},
    # Track info
    "track":    None,
    "playing":  False,
    "position": 0,
    # Lyrics
    "lyrics":   None,
    "lyrics_line": "",
    "lyrics_position": 0,
    # Auto mode
    "auto":     {},
    "auto_active": False,
}
_last_beat_seq = -1
_beat_flash_until = 0.0


# ── Helpers communication ──────────────────────────────────────────────────────

def _fetch_state() -> dict:
    if _get_state_fn:
        return _get_state_fn()
    try:
        import urllib.request, json
        with urllib.request.urlopen(_HTTP_BASE + "/api/status", timeout=0.5) as r:
            return json.loads(r.read())
    except Exception:
        return {}

def _post(path: str):
    parts = [p for p in path.split("/") if p]
    if parts[1] == "mode" and _switch_fn:
        _do_mode_direct(parts[2]); return
    if parts[1] == "hud" and _switch_hud_fn:
        _do_hud_direct(parts[2]); return
    if parts[1] == "led" and _set_led_fn:
        _set_led_fn(parts[2], parts[3]); return
    try:
        import urllib.request
        urllib.request.urlopen(
            urllib.request.Request(_HTTP_BASE + path, method="POST"),
            timeout=0.5
        )
    except Exception:
        pass

def _do_mode_direct(val):
    cur = _state.get("mode", _MODES[0])
    if val in ("next", "prev"):
        _switch_fn(+1 if val == "next" else -1)
    elif val in _MODES and val != cur:
        idx_cur = _MODES.index(cur) if cur in _MODES else 0
        idx_tgt = _MODES.index(val)
        _switch_fn(idx_tgt - idx_cur)

def _do_hud_direct(val):
    cur = _state.get("hud", _HUD_STYLES[0])
    if val in ("next", "prev"):
        _switch_hud_fn(+1 if val == "next" else -1)
    elif val in _HUD_STYLES and val != cur:
        idx_cur = _HUD_STYLES.index(cur) if cur in _HUD_STYLES else 0
        idx_tgt = _HUD_STYLES.index(val)
        _switch_hud_fn(idx_tgt - idx_cur)


# ── Tags DPG (évite les magic strings) ────────────────────────────────────────
TAG_WIN       = "main_win"
TAG_BPM_TEXT  = "bpm_text"
TAG_BPM_BAR   = "bpm_bar"
TAG_LVL_TEXT  = "lvl_text"
TAG_LVL_BAR   = "lvl_bar"
TAG_BEAT_IND  = "beat_indicator"
TAG_STATUS    = "status_text"
# Track info tags
TAG_TRACK     = "track_text"
TAG_PLAYING   = "playing_indicator"
TAG_POSITION  = "position_text"
# Lyrics tags
TAG_LYRICS    = "lyrics_text"
TAG_LYRICS_PROGRESS = "lyrics_progress"
# Lyrics Manager tags
TAG_LYR_ARTIST = "lyr_artist"
TAG_LYR_TITLE  = "lyr_title"
TAG_LYR_FEEDBACK = "lyr_feedback"
# Auto mode tags
TAG_AUTO_MODE = "auto_mode_text"
TAG_AUTO_SCORE = "auto_score_text"


# ── Callbacks boutons ──────────────────────────────────────────────────────────

def _on_led(sender, app_data, user_data):
    _post(f"/api/led/{user_data[0]}/{user_data[1]}")

def _on_mode(sender, app_data, user_data):
    _post(f"/api/mode/{user_data}")

def _on_hud(sender, app_data, user_data):
    _post(f"/api/hud/{user_data}")


def _on_lyrics_search(sender, app_data, user_data):
    """Recherche manuelle de lyrics"""
    artist = dpg.get_value(TAG_LYR_ARTIST)
    title = dpg.get_value(TAG_LYR_TITLE)

    if not artist or not title:
        dpg.set_value(TAG_LYR_FEEDBACK, "Error: artist and title required")
        return

    try:
        import urllib.request
        import json
        data = json.dumps({"artist": artist, "title": title}).encode()
        req = urllib.request.Request(
            f"{_HTTP_BASE}/api/lyrics-manual-search",
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            if result.get("error"):
                dpg.set_value(TAG_LYR_FEEDBACK, f"Error: {result['error']}")
            else:
                dpg.set_value(TAG_LYR_FEEDBACK, f"Found: {result['source']} ({result['score']:.0f}pts)")
    except Exception as e:
        dpg.set_value(TAG_LYR_FEEDBACK, f"Error: {e}")


def _on_lyrics_flag_temp(sender, app_data, user_data):
    """Skip temporaire (session)"""
    lyrics = dpg.get_value(TAG_LYRICS) if dpg.does_item_exist(TAG_LYRICS) else ""
    # Récupérer les infos depuis l'état global si dispo
    try:
        import urllib.request
        import json

        # Pour l'instant, utilise une valeur placeholder car on n'a pas accès
        # aux détails de la source depuis l'UI simple
        data = json.dumps({
            "artist": "current",
            "title": "track",
            "source": "unknown"
        }).encode()
        req = urllib.request.Request(
            f"{_HTTP_BASE}/api/lyrics-flag-temp",
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=5)
        dpg.set_value(TAG_LYR_FEEDBACK, "Skipped (session)")
    except Exception as e:
        dpg.set_value(TAG_LYR_FEEDBACK, f"Error: {e}")


def _on_lyrics_flag_perm(sender, app_data, user_data):
    """Blacklist permanent"""
    try:
        import urllib.request
        import json
        data = json.dumps({
            "artist": "current",
            "title": "track",
            "source": "unknown",
            "reason": "user_blacklisted"
        }).encode()
        req = urllib.request.Request(
            f"{_HTTP_BASE}/api/lyrics-flag-perm",
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=5)
        dpg.set_value(TAG_LYR_FEEDBACK, "Blacklisted (permanent)")
    except Exception as e:
        dpg.set_value(TAG_LYR_FEEDBACK, f"Error: {e}")


# ── Boucle de polling ──────────────────────────────────────────────────────────

def _poll_loop():
    global _last_beat_seq, _beat_flash_until
    while dpg.is_dearpygui_running():
        s = _fetch_state()
        if s:
            _state.update(s)

            # BPM
            bpm = s.get("bpm")
            dpg.set_value(TAG_BPM_TEXT, f"{round(bpm)} BPM" if bpm else "— BPM")
            dpg.set_value(TAG_BPM_BAR,
                min(1.0, (bpm - 60) / 100) if bpm else 0.0)

            # Level
            lvl = s.get("level", 0.0)
            dpg.set_value(TAG_LVL_TEXT, f"{int(lvl * 100)}%")
            dpg.set_value(TAG_LVL_BAR, lvl)

            # Beat flash
            seq = s.get("beat_seq", 0)
            if seq != _last_beat_seq:
                _last_beat_seq = seq
                _beat_flash_until = time.time() + 0.12

            now = time.time()
            if now < _beat_flash_until:
                dpg.configure_item(TAG_BEAT_IND, color=list(_ORANGE))
            else:
                dpg.configure_item(TAG_BEAT_IND, color=[*_ORANGE[:3], 40])

            # Track info
            track = s.get("track")
            playing = s.get("playing", False)
            position = s.get("position", 0)

            if track:
                dpg.set_value(TAG_TRACK, track)
            else:
                dpg.set_value(TAG_TRACK, "No track playing")

            # Playing indicator
            dpg.configure_item(TAG_PLAYING,
                color=list(_GREEN) if playing else [*_GREEN[:3], 40])

            # Position display
            pos_mins = int(position // 60)
            pos_secs = int(position % 60)
            dpg.set_value(TAG_POSITION, f"{pos_mins}:{pos_secs:02d}")

            # Lyrics
            lyrics = s.get("lyrics") or {}
            if lyrics and not lyrics.get("loading", False):
                # Get current line from lyrics lines
                lines = lyrics.get("lines", [])
                current_idx = lyrics.get("current_idx", 0)

                if lines and 0 <= current_idx < len(lines):
                    current_line = lines[current_idx].get("text", "")
                    dpg.set_value(TAG_LYRICS, current_line)

                    # Progress through lyrics
                    progress = (current_idx + 1) / len(lines) if lines else 0
                    dpg.set_value(TAG_LYRICS_PROGRESS, progress)
                else:
                    dpg.set_value(TAG_LYRICS, "No lyrics available")
                    dpg.set_value(TAG_LYRICS_PROGRESS, 0.0)
            else:
                if lyrics and lyrics.get("loading", False):
                    dpg.set_value(TAG_LYRICS, "Loading lyrics...")
                else:
                    dpg.set_value(TAG_LYRICS, "No lyrics")

            # Auto mode status
            auto = s.get("auto", {})
            if auto:
                current_mode = auto.get("current_mode", "Unknown")
                scores = auto.get("scores", {})

                # Show current auto mode
                mode_label = current_mode.replace("_", " ").upper()
                dpg.set_value(TAG_AUTO_MODE, f"AUTO: {mode_label}")

                # Show top scoring mode
                if scores:
                    best_mode = max(scores.keys(), key=lambda k: scores[k])
                    best_score = scores[best_mode]
                    dpg.set_value(TAG_AUTO_SCORE, f"{best_mode}: {best_score:.0f}%")
            else:
                dpg.set_value(TAG_AUTO_MODE, "AUTO: OFF")
                dpg.set_value(TAG_AUTO_SCORE, "")

            # Boutons actifs
            for m in _MODES:
                tag = f"btn_mode_{m}"
                if dpg.does_item_exist(tag):
                    active = (m == s.get("mode"))
                    dpg.configure_item(tag,
                        enabled=True)
                    _set_btn_theme(tag, active, kind="mode")

            for h in _HUD_STYLES:
                tag = f"btn_hud_{h}"
                if dpg.does_item_exist(tag):
                    active = (h == s.get("hud"))
                    _set_btn_theme(tag, active, kind="hud")

            # LED actifs
            led = s.get("led", {})
            for m in _CASE_MODES:
                tag = f"btn_case_{m}"
                if dpg.does_item_exist(tag):
                    _set_btn_theme(tag, m == led.get("case_mode"), kind="hud")
            for m in _FAN_MODES:
                tag = f"btn_fan_{m}"
                if dpg.does_item_exist(tag):
                    _set_btn_theme(tag, m == led.get("fan_mode"), kind="hud")

            # LED colors - highlight active colors
            for c in _LED_COLORS:
                # Case primary colors
                tag = f"btn_case_col_{c}"
                if dpg.does_item_exist(tag):
                    active = (c == led.get("case_color"))
                    _set_color_btn_theme(tag, active, c)

                # Case secondary colors
                tag = f"btn_case_col2_{c}"
                if dpg.does_item_exist(tag):
                    active = (c == led.get("case_color2"))
                    _set_color_btn_theme(tag, active, c)

                # Fans primary colors
                tag = f"btn_fan_col_{c}"
                if dpg.does_item_exist(tag):
                    active = (c == led.get("fan_color"))
                    _set_color_btn_theme(tag, active, c)

                # Fans secondary colors
                tag = f"btn_fan_col2_{c}"
                if dpg.does_item_exist(tag):
                    active = (c == led.get("fan_color2"))
                    _set_color_btn_theme(tag, active, c)

            mode = s.get("mode", "?")
            hud  = s.get("hud",  "?")
            dpg.set_value(TAG_STATUS, f"{mode}  ·  {hud}")

        time.sleep(0.25)


# ── Thèmes boutons ─────────────────────────────────────────────────────────────

_theme_mode_active   = None
_theme_mode_inactive = None
_theme_hud_active    = None
_theme_hud_inactive  = None

def _build_themes():
    global _theme_mode_active, _theme_mode_inactive
    global _theme_hud_active,  _theme_hud_inactive

    # Mode actif — violet
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (124,  0, 224, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (160, 40, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (200, 64, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,           (255, 255, 255, 255))
    _theme_mode_active = t

    # Mode inactif
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (25,  18,  50, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (50,  35,  90, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (80,  60, 140, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,           *[list(_MUTED)])
    _theme_mode_inactive = t

    # HUD actif — bleu
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (0,   80, 130, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (0,  110, 170, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (56, 191, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,           (255, 255, 255, 255))
    _theme_hud_active = t

    # HUD inactif
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,        (15,  12,  35, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (30,  22,  60, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (50,  40,  90, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text,           *[list(_MUTED)])
    _theme_hud_inactive = t


def _set_btn_theme(tag, active: bool, kind: str):
    if kind == "mode":
        dpg.bind_item_theme(tag, _theme_mode_active if active else _theme_mode_inactive)
    else:
        dpg.bind_item_theme(tag, _theme_hud_active if active else _theme_hud_inactive)


def _set_color_btn_theme(tag, active: bool, color_name: str):
    """Highlight active color button with white border."""
    if active:
        # Create a theme with white border for active color
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                base_color = list(_LED_PREVIEWS.get(color_name, _GREEN))
                dpg.add_theme_color(dpg.mvThemeCol_Button, base_color)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, base_color)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, base_color)
                # Add white border for active state
                dpg.add_theme_color(dpg.mvThemeCol_Border, (255, 255, 255, 255))
        dpg.bind_item_theme(tag, t)
    else:
        # Use the standard color button theme
        col = list(_LED_PREVIEWS.get(color_name, _GREEN))
        t = _make_color_btn_theme(col)
        dpg.bind_item_theme(tag, t)


# ── Construction UI ────────────────────────────────────────────────────────────

def _build_ui():
    # Thème global sombre
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,        list(_BG))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,         list(_PANEL))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,         (20, 15, 40, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,  (35, 25, 65, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Border,          list(_BORDER))
            dpg.add_theme_color(dpg.mvThemeCol_Text,            list(_TEXT))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,         (10,  7, 25, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,   (30, 20, 60, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,   list(_ACCENT))
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,  8)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,   4)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,     8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,   16, 16)
    dpg.bind_theme(global_theme)

    _build_themes()

    W, H = 540, 900

    with dpg.window(label="Box Screen", tag=TAG_WIN,
                    width=W, height=H, no_resize=True,
                    no_collapse=True):

        # ── Titre ──────────────────────────────────────────────────────────
        dpg.add_text("BOX SCREEN", color=list(_ACCENT))
        dpg.add_text("Trofeo Vision 9.16 — 1920×462",
                     color=list(_MUTED))
        dpg.add_separator()
        dpg.add_spacer(height=4)

        # ── Track Info ────────────────────────────────────────────────────────
        with dpg.child_window(height=80, border=True):
            with dpg.group(horizontal=True):
                dpg.add_text("NOW PLAYING", color=list(_MUTED))
                dpg.add_spacer(width=8)
                # Playing indicator
                dpg.add_text("●", tag=TAG_PLAYING, color=[*_GREEN[:3], 40])

            dpg.add_spacer(height=4)

            # Track name
            dpg.add_text("No track", tag=TAG_TRACK, color=list(_TEXT),
                         wrap=-1)

            dpg.add_spacer(height=4)

            # Position/Time
            with dpg.group(horizontal=True):
                dpg.add_text("0:00", tag=TAG_POSITION, color=list(_MUTED))
                dpg.add_spacer(width=-1)

        dpg.add_spacer(height=8)

        # ── Lyrics ────────────────────────────────────────────────────────────
        with dpg.child_window(height=100, border=True):
            dpg.add_text("LYRICS", color=list(_MUTED))
            dpg.add_spacer(height=4)

            # Current lyrics line
            dpg.add_text("No lyrics", tag=TAG_LYRICS, color=list(_ACCENT),
                         wrap=-1)

            dpg.add_spacer(height=8)

            # Lyrics progress
            _prog_theme_lyrics = _make_progress_theme(_ACCENT)
            pb = dpg.add_progress_bar(tag=TAG_LYRICS_PROGRESS, default_value=0.0,
                                      width=-1, height=4)
            dpg.bind_item_theme(pb, _prog_theme_lyrics)

        dpg.add_spacer(height=8)

        # ── Lyrics Manager ─────────────────────────────────────────────────────
        with dpg.child_window(height=120, border=True):
            dpg.add_text("LYRICS MANAGER", color=list(_MUTED))
            dpg.add_spacer(height=4)

            # Recherche manuelle
            with dpg.group(horizontal=True):
                dpg.add_input_text(label="Artist", tag=TAG_LYR_ARTIST, width=100)
                dpg.add_input_text(label="Title", tag=TAG_LYR_TITLE, width=140)
                dpg.add_button(label="Search", callback=_on_lyrics_search, width=70)

            # Validation
            with dpg.group(horizontal=True):
                dpg.add_button(label="Skip Session", callback=_on_lyrics_flag_temp, width=110)
                dpg.add_button(label="Blacklist", callback=_on_lyrics_flag_perm, width=110)

            # Feedback
            dpg.add_text("", tag=TAG_LYR_FEEDBACK, color=list(_GREEN))

        dpg.add_spacer(height=8)

        # ── Mode ───────────────────────────────────────────────────────────
        with dpg.child_window(height=80, border=True):
            dpg.add_text("MODE", color=list(_MUTED))
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                for m in _MODES:
                    label = m.replace("_", " ").upper()
                    tag   = f"btn_mode_{m}"
                    dpg.add_button(label=label, tag=tag, width=130, height=30,
                                   callback=_on_mode, user_data=m)
                    dpg.bind_item_theme(tag, _theme_mode_inactive)

        dpg.add_spacer(height=8)

        # ── HUD ────────────────────────────────────────────────────────────
        with dpg.child_window(height=100, border=True):
            dpg.add_text("HUD STYLE", color=list(_MUTED))
            dpg.add_spacer(height=4)
            # 2 rows of buttons
            for row in range(2):
                with dpg.group(horizontal=True):
                    start = row * 5
                    end = min(start + 5, len(_HUD_STYLES))
                    for h in _HUD_STYLES[start:end]:
                        tag = f"btn_hud_{h}"
                        dpg.add_button(label=h.upper(), tag=tag, width=58, height=26,
                                       callback=_on_hud, user_data=h)
                        dpg.bind_item_theme(tag, _theme_hud_inactive)
                if row == 0:
                    dpg.add_spacer(height=4)

        dpg.add_spacer(height=8)

        # ── Audio ──────────────────────────────────────────────────────────
        with dpg.child_window(height=150, border=True):
            with dpg.group(horizontal=True):
                dpg.add_text("AUDIO", color=list(_MUTED))
                dpg.add_spacer(width=8)
                dpg.add_text("[BEAT]", tag=TAG_BEAT_IND,
                             color=[*_ORANGE[:3], 40])

            dpg.add_spacer(height=6)

            # BPM
            dpg.add_text("— BPM", tag=TAG_BPM_TEXT, color=list(_GREEN))
            _prog_theme_bpm = _make_progress_theme(_GREEN)
            pb = dpg.add_progress_bar(tag=TAG_BPM_BAR, default_value=0.0,
                                      width=-1, height=8)
            dpg.bind_item_theme(pb, _prog_theme_bpm)

            dpg.add_spacer(height=10)

            # Level
            dpg.add_text("LEVEL (BASS)", color=list(_MUTED))
            dpg.add_text("0%", tag=TAG_LVL_TEXT, color=list(_BLUE))
            _prog_theme_lvl = _make_progress_theme(_BLUE)
            pb2 = dpg.add_progress_bar(tag=TAG_LVL_BAR, default_value=0.0,
                                       width=-1, height=8)
            dpg.bind_item_theme(pb2, _prog_theme_lvl)

        dpg.add_spacer(height=8)

        # ── LEDs ───────────────────────────────────────────────────────────
        with dpg.child_window(height=200, border=True):
            # Case mode + colors
            with dpg.group(horizontal=True):
                dpg.add_text("CASE", color=list(_MUTED))
                dpg.add_spacer(width=8)
                for m in _CASE_MODES:
                    tag = f"btn_case_{m}"
                    dpg.add_button(label=m.upper(), tag=tag, width=52, height=24,
                                   callback=_on_led, user_data=("case", m))
                    dpg.bind_item_theme(tag, _theme_hud_inactive)
                dpg.add_spacer(width=12)
                # Primary colors
                for c in _LED_COLORS:
                    tag = f"btn_case_col_{c}"
                    col = list(_LED_PREVIEWS[c])
                    dpg.add_button(label=" ", tag=tag, width=18, height=24,
                                   callback=_on_led, user_data=("case_color", c))
                    _t = _make_color_btn_theme(col)
                    dpg.bind_item_theme(tag, _t)

            dpg.add_spacer(height=6)

            # Case secondary colors (color2)
            with dpg.group(horizontal=True):
                dpg.add_text("CASE 2", color=list(_MUTED))
                dpg.add_spacer(width=8)
                dpg.add_spacer(width=130)  # Skip mode buttons space
                dpg.add_spacer(width=12)
                # Secondary colors
                for c in _LED_COLORS:
                    tag = f"btn_case_col2_{c}"
                    col = list(_LED_PREVIEWS[c])
                    dpg.add_button(label=" ", tag=tag, width=18, height=24,
                                   callback=_on_led, user_data=("case_color2", c))
                    _t = _make_color_btn_theme(col)
                    dpg.bind_item_theme(tag, _t)

            dpg.add_spacer(height=10)

            # Fans mode + colors
            with dpg.group(horizontal=True):
                dpg.add_text("FANS", color=list(_MUTED))
                dpg.add_spacer(width=8)
                fan_labels = {"off":"OFF","spin":"SPIN","static":"STATIC","beat_pulse":"BEAT"}
                for m in _FAN_MODES:
                    tag = f"btn_fan_{m}"
                    dpg.add_button(label=fan_labels[m], tag=tag, width=52, height=24,
                                   callback=_on_led, user_data=("fans", m))
                    dpg.bind_item_theme(tag, _theme_hud_inactive)
                dpg.add_spacer(width=12)
                # Primary colors
                for c in _LED_COLORS:
                    tag = f"btn_fan_col_{c}"
                    col = list(_LED_PREVIEWS[c])
                    dpg.add_button(label=" ", tag=tag, width=18, height=24,
                                   callback=_on_led, user_data=("fans_color", c))
                    _t = _make_color_btn_theme(col)
                    dpg.bind_item_theme(tag, _t)

            dpg.add_spacer(height=6)

            # Fans secondary colors (color2)
            with dpg.group(horizontal=True):
                dpg.add_text("FANS 2", color=list(_MUTED))
                dpg.add_spacer(width=8)
                dpg.add_spacer(width=130)  # Skip mode buttons space
                dpg.add_spacer(width=12)
                # Secondary colors
                for c in _LED_COLORS:
                    tag = f"btn_fan_col2_{c}"
                    col = list(_LED_PREVIEWS[c])
                    dpg.add_button(label=" ", tag=tag, width=18, height=24,
                                   callback=_on_led, user_data=("fans_color2", c))
                    _t = _make_color_btn_theme(col)
                    dpg.bind_item_theme(tag, _t)

        dpg.add_spacer(height=8)

        # ── Auto Mode Status ────────────────────────────────────────────────────
        with dpg.child_window(height=60, border=True):
            dpg.add_text("AUTO MODE", color=list(_MUTED))
            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                dpg.add_text("AUTO: OFF", tag=TAG_AUTO_MODE, color=list(_ACCENT))
                dpg.add_spacer(width=-1)
                dpg.add_text("", tag=TAG_AUTO_SCORE, color=list(_MUTED))

        dpg.add_spacer(height=8)

        # ── Status bar ─────────────────────────────────────────────────────
        dpg.add_text("—", tag=TAG_STATUS, color=list(_MUTED))


def _make_color_btn_theme(color):
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button,       color)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, color)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  color)
    return t

def _make_progress_theme(color):
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvProgressBar):
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, list(color))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,       (20, 15, 40, 255))
    return t


# ── Point d'entrée ─────────────────────────────────────────────────────────────

def run_embedded(switch_fn, switch_hud_fn, get_state_fn, set_led_fn=None):
    """Appelé depuis launcher.py pour tourner sans serveur HTTP."""
    global _switch_fn, _switch_hud_fn, _get_state_fn, _set_led_fn
    _switch_fn     = switch_fn
    _switch_hud_fn = switch_hud_fn
    _get_state_fn  = get_state_fn
    _set_led_fn    = set_led_fn
    _run()


def _run():
    dpg.create_context()
    dpg.create_viewport(title="Box Screen", width=560, height=920,
                        small_icon="", large_icon="",
                        resizable=False)
    dpg.setup_dearpygui()
    _build_ui()
    dpg.show_viewport()
    dpg.set_primary_window(TAG_WIN, True)

    # Thread de polling
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    dpg.start_dearpygui()
    dpg.destroy_context()


if __name__ == "__main__":
    # Mode standalone : poll via HTTP sur localhost:7420
    # Lance launcher.py en premier (ou en parallèle)
    print("Box Screen Dashboard — http://localhost:7420 doit être actif")
    print("(ou lance : python launcher.py  dans un autre terminal)")
    _run()
