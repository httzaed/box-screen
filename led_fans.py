"""
led_fans.py — Contrôleur LED pour Box Screen
  Mapping réel (led_map.json) :
    fans  : indices 0-14   (15 LEDs en parallèle sur tous les fans)
    case  : indices 15-49  (35 LEDs)
    skip  : indices 50-119 (ignorées)

  Modes case  : off | wave | beat
  Modes fans  : off | spin_bpm | spin_fixed | static
"""
import time
import math
import json
import threading
import subprocess
from pathlib import Path

from openrgb.utils import RGBColor

# ── Mapping (chargé depuis led_map.json, sinon valeurs connues) ───────────────
_HERE = Path(__file__).parent

def _load_map():
    try:
        data = json.loads((_HERE / "led_map.json").read_text())
        groups     = data["groups"]
        zone_idx   = data["zone_idx"]
        zone_offset = data["zone_offset"]
        fans_idxs  = sorted(groups.get("fans", []))
        case_idxs  = sorted(groups.get("case", []))
        mb_idxs    = sorted(groups.get("mainboard", []))
        return zone_idx, zone_offset, fans_idxs, case_idxs, mb_idxs
    except Exception as e:
        print(f"[LED] led_map.json introuvable ou invalide ({e}) — valeurs par défaut")
        return 1, 0, list(range(0, 15)), list(range(15, 50)), []

ZONE_IDX, ZONE_OFFSET, FAN_IDXS, CASE_IDXS, MB_IDXS = _load_map()
FAN_LEDS  = len(FAN_IDXS)   # 15 — position 0-14 = 1 LED sur chaque fan simultanément
FRAME_TIME = 0.020           # ~50 fps

# ── Palette ───────────────────────────────────────────────────────────────────
COLORS = {
    "violet": (180,   0, 255),
    "cyan":   (  0, 200, 255),
    "blue":   (  0,  80, 255),
    "teal":   (  0, 210, 150),
    "green":  (  0, 255, 100),
    "yellow": (255, 200,   0),
    "orange": (255,  85,   0),
    "red":    (255,   0,   0),
    "pink":   (255,  20, 100),
    "white":  (255, 255, 255),
}

# ── Config runtime ────────────────────────────────────────────────────────────
_CFG_FILE = _HERE / "led_config.json"

_CFG_DEFAULTS = {
    "case_mode":   "beat",
    "fan_mode":    "spin",
    "case_color":  "violet",
    "case_color2": "cyan",
    "fan_color":   "violet",
    "fan_color2":  "cyan",
    "case_speed":  1.0,
    "fan_speed":   1.0,
    "case_length": 1.0,
    "brightness":  1.0,
    "case_decay":  0.45,
}

def _load_cfg():
    try:
        saved = json.loads(_CFG_FILE.read_text())
        merged = dict(_CFG_DEFAULTS)
        for k, v in saved.items():
            if k in merged:
                merged[k] = v
        print(f"[LED] config chargée depuis {_CFG_FILE.name}")
        return merged
    except Exception:
        return dict(_CFG_DEFAULTS)

def _save_cfg(cfg):
    try:
        _CFG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        print(f"[LED] save config: {e}")

_cfg_lock = threading.Lock()
_cfg = _load_cfg()

def get_cfg() -> dict:
    with _cfg_lock:
        return dict(_cfg)

def set_cfg(**kwargs):
    with _cfg_lock:
        for k, v in kwargs.items():
            if k in _cfg:
                _cfg[k] = v
        _save_cfg(dict(_cfg))


# ── OpenRGB singleton ─────────────────────────────────────────────────────────
def _connect():
    try:
        import led_wave as _lw
        if not _lw._ensure_server():
            return False, None
        device = _lw._device
        print(f"[LED] {device.name}  —  zones: {[z.name for z in device.zones]}")
        return True, device
    except Exception as e:
        print(f"[LED] connexion échouée: {e}")
        return False, None


# ── Vitesse rotation ──────────────────────────────────────────────────────────
def _rot_step() -> float:
    try:
        import bpm_source
        bpm = bpm_source.get_bpm()
        if bpm is not None:
            # 1 tour complet par beat = FAN_LEDS positions en 60/bpm secondes
            return FAN_LEDS * (bpm / 60.0) * FRAME_TIME
    except ImportError:
        pass
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=fan.speed", "--format=csv,noheader,nounits"],
            text=True, timeout=1
        ).strip()
        pct = float(out.split("\n")[0])
        return 0.15 + (0.55 - 0.15) * (pct / 100.0)
    except Exception:
        return 0.25


# ── Animation ─────────────────────────────────────────────────────────────────
def _clamp(v): return max(0, min(255, int(v)))

def run(stop_event: threading.Event, get_colors=None):
    import traceback
    while not stop_event.is_set():
        try:
            _run_inner(stop_event)
        except Exception:
            traceback.print_exc()
            time.sleep(1)


def _run_inner(stop_event):
    ok, device = _connect()
    if not ok:
        return

    total_leds  = len(device.leds)
    all_colors  = [RGBColor(0, 0, 0)] * total_leds

    # BPM source
    try:
        import bpm_source
        bpm_source.start()
        print("[LED] BPM source démarrée")
    except Exception as e:
        print(f"[LED] BPM source: {e}")

    beat_flash_t  = 0.0
    last_beat_seq = -1
    angle         = 0.0   # shared spin/comet offset
    wave_off      = 0.0
    hue_off       = 0.0
    breathe_t     = 0.0

    import colorsys

    print(f"[LED] fans={FAN_IDXS}  case={CASE_IDXS[0]}..{CASE_IDXS[-1]}  total={total_leds}")

    while not stop_event.is_set():
        now = time.monotonic()
        cfg = get_cfg()

        bri        = cfg["brightness"]
        case_spd   = cfg["case_speed"]
        fan_spd    = cfg["fan_speed"]
        n_case     = max(1, int(len(CASE_IDXS) * cfg["case_length"]))
        decay      = max(0.05, cfg["case_decay"])

        # Beat
        try:
            import bpm_source as _bs
            _, beat_seq = _bs.get_beat_event()
            if beat_seq != last_beat_seq:
                last_beat_seq = beat_seq
                beat_flash_t  = now
        except Exception:
            pass
        beat_bright = max(0.0, 1.0 - (now - beat_flash_t) / decay)

        def c(name):
            return COLORS.get(name, COLORS["violet"])
        def lerp(c1, c2, t):
            return (int(c1[0]+(c2[0]-c1[0])*t), int(c1[1]+(c2[1]-c1[1])*t), int(c1[2]+(c2[2]-c1[2])*t))
        def mk(r, g, b, intensity=1.0):
            return RGBColor(_clamp(r*bri*intensity), _clamp(g*bri*intensity), _clamp(b*bri*intensity))

        def animate_zone(mode, idxs, col1, col2, speed, n_lit, beat_b):
            """Apply one mode to a list of LED indices. Returns updated offsets via side-effects on shared state."""
            nonlocal angle, wave_off, hue_off, breathe_t
            N = len(idxs)
            nl = min(N, max(1, n_lit))
            dark_idxs = idxs[nl:]
            active    = idxs[:nl]

            for idx in dark_idxs:
                all_colors[ZONE_OFFSET + idx] = RGBColor(0,0,0)

            if mode == "off":
                for idx in idxs:
                    all_colors[ZONE_OFFSET + idx] = RGBColor(0,0,0)

            elif mode == "static":
                for idx in active:
                    all_colors[ZONE_OFFSET + idx] = mk(*col1)

            elif mode == "wave":
                wave_off = math.fmod(wave_off + 0.6 * speed, nl * 2)
                cycle = nl * 2
                for pos, idx in enumerate(active):
                    t = ((pos + int(wave_off)) % cycle) / cycle
                    b = 0.3 + 0.7 * abs(math.sin(t * math.pi))
                    all_colors[ZONE_OFFSET + idx] = mk(col1[0]*b, col1[1]*b, col1[2]*b)

            elif mode == "beat":
                for idx in active:
                    all_colors[ZONE_OFFSET + idx] = mk(col1[0], col1[1], col1[2], beat_b)

            elif mode == "beat_pulse":
                # Amplitude/RMS : l'énergie basse normalisée pilote la luminosité en continu
                # (bpm_source.get_level() = envelope follower asymétrique, attack 5ms / decay ~80ms,
                #  normalisé sur percentile 95 glissant — identique au meilleur algo du visualiseur HTML)
                try:
                    import bpm_source as _bs
                    level = _bs.get_level()
                except Exception:
                    level = 0.0
                for idx in active:
                    all_colors[ZONE_OFFSET + idx] = mk(col1[0], col1[1], col1[2], level)

            elif mode == "gradient":
                wave_off = math.fmod(wave_off + 0.4 * speed, nl)
                for pos, idx in enumerate(active):
                    t = math.fmod((pos + wave_off) / nl, 1.0)
                    col = lerp(col1, col2, t)
                    all_colors[ZONE_OFFSET + idx] = mk(*col)

            elif mode == "rainbow":
                hue_off = math.fmod(hue_off + 0.003 * speed, 1.0)
                for pos, idx in enumerate(active):
                    h = math.fmod(hue_off + pos / nl, 1.0)
                    r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
                    all_colors[ZONE_OFFSET + idx] = mk(r*255, g*255, b*255)

            elif mode == "comet":
                wave_off = math.fmod(wave_off + 0.8 * speed, nl)
                head = int(wave_off)
                tail = min(nl, 8)
                for pos, idx in enumerate(active):
                    dist = (pos - head) % nl
                    if dist == 0:
                        all_colors[ZONE_OFFSET + idx] = mk(*col1)
                    elif dist < tail:
                        t = 1.0 - dist / tail
                        col = lerp(col2, (0,0,0), 1.0 - t)
                        all_colors[ZONE_OFFSET + idx] = mk(*col)
                    else:
                        all_colors[ZONE_OFFSET + idx] = RGBColor(0,0,0)

            elif mode == "breathe":
                breathe_t += 0.015 * speed
                intensity = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(breathe_t))
                for idx in active:
                    all_colors[ZONE_OFFSET + idx] = mk(*col1, intensity)

            elif mode == "spin":
                rot = _rot_step() * speed
                angle = math.fmod(angle + rot, nl)
                for pos, idx in enumerate(active):
                    dist  = (pos - angle) % nl
                    t     = dist / nl
                    b_led = max(0.0, 1.0 - t * 2.5)
                    dark  = 0.06
                    all_colors[ZONE_OFFSET + idx] = mk(
                        col1[0]*(b_led+dark), col1[1]*(b_led+dark), col1[2]*(b_led+dark))

            elif mode == "dual":
                rot = _rot_step() * speed
                angle = math.fmod(angle + rot, nl)
                for pos, idx in enumerate(active):
                    dist  = (pos - angle) % nl
                    t     = dist / nl
                    col   = col1 if t < 0.5 else col2
                    b_led = max(0.0, 1.0 - (t % 0.5) * 4.0)
                    dark  = 0.06
                    all_colors[ZONE_OFFSET + idx] = mk(
                        col[0]*(b_led+dark), col[1]*(b_led+dark), col[2]*(b_led+dark))

        # ── Fans ─────────────────────────────────────────────────────────
        animate_zone(cfg["fan_mode"], FAN_IDXS,
                     c(cfg["fan_color"]), c(cfg["fan_color2"]),
                     fan_spd, len(FAN_IDXS), beat_bright)

        # ── Case ─────────────────────────────────────────────────────────
        animate_zone(cfg["case_mode"], CASE_IDXS,
                     c(cfg["case_color"]), c(cfg["case_color2"]),
                     case_spd, n_case, beat_bright)

        device.set_colors(all_colors)
        time.sleep(FRAME_TIME)


def shutdown():
    import led_wave as _lw
    _lw.shutdown()
