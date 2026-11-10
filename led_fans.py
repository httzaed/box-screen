"""
led_fans.py — Contrôleur LED pour Box Screen
  Mapping réel (led_map.json) :
    fans     : indices 0-14   (15 LEDs en parallèle sur tous les fans)
    case     : indices 15-49  (35 LEDs)
    keyboard : device séparé (Logitech G915, 117 LEDs)
    skip     : indices 50-119 (ignorées sur le device principal)

  Modes case     : off | wave | beat
  Modes fans     : off | spin_bpm | spin_fixed | static | beat_pulse | beat_pulse_dual
  Modes keyboard : off | static | wave | beat_pulse | beat_pulse_dual | sync_fans

  beat_pulse_dual : mix de fan_color (basses) et fan_color2 (aigus) en temps réel
  sync_fans      : couleur des fans sur le clavier, luminosité fixe sans pulsation
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
        groups       = data["groups"]
        zone_idx     = data["zone_idx"]
        zone_offset  = data["zone_offset"]
        fans_idxs    = sorted(groups.get("fans", []))
        case_idxs    = sorted(groups.get("case", []))
        mb_idxs      = sorted(groups.get("mainboard", []))

        # Clavier (device séparé)
        kb_cfg = data.get("keyboard", {})
        kb_device_idx = kb_cfg.get("device_idx", None)
        kb_leds = kb_cfg.get("leds", 117)  # Valeur par défaut pour G915

        return zone_idx, zone_offset, fans_idxs, case_idxs, mb_idxs, kb_device_idx, kb_leds
    except Exception as e:
        print(f"[LED] led_map.json introuvable ou invalide ({e}) — valeurs par défaut")
        return 1, 0, list(range(0, 15)), list(range(15, 50)), [], None, 117

ZONE_IDX, ZONE_OFFSET, FAN_IDXS, CASE_IDXS, MB_IDXS, KEYBOARD_DEVICE_IDX, KEYBOARD_LEDS = _load_map()

def _mapped_indices(device):
    """Validate the saved layout before using it on the connected hardware."""
    total = len(device.leds)
    groups = (FAN_IDXS, CASE_IDXS, MB_IDXS)
    if any(not 0 <= ZONE_OFFSET + idx < total for group in groups for idx in group):
        print(f"[LED] Mapping incompatible avec '{device.name}' ({total} LEDs, "
              f"offset={ZONE_OFFSET}) — fans/case désactivés. "
              "Configurer les zones ARGB dans OpenRGB puis refaire led_map.json avec map_leds.py.")
        return [], []
    return list(FAN_IDXS), list(CASE_IDXS)

FAN_LEDS  = len(FAN_IDXS)   # 15 — position 0-14 = 1 LED sur chaque fan simultanément
FRAME_TIME = 0.020           # ~50 fps
_led_timing = {}

def get_timing():
    return dict(_led_timing)

last_progress = time.monotonic()


def _pulse_envelope(previous, target, dt):
    """Fast kick attack, soft release, and no residual glow in silence."""
    target = min(1.0, max(0.0, target))
    tau = 0.008 if target > previous else 0.075
    level = previous + (target - previous) * (-math.expm1(-max(0.0, dt) / tau))
    return 0.0 if target < 0.003 and level < 0.003 else level


def _send_frame(device, colors, keyboard, keyboard_colors):
    """Stream colors without device-state reads; isolate keyboard failures."""
    if device is not None:
        device.set_colors(colors, fast=True)
    if keyboard is not None:
        try:
            keyboard.set_colors(keyboard_colors, fast=True)
        except Exception as error:
            return error
    return None

# ── Clavier OpenRGB ─────────────────────────────────────────────────────────────
_keyboard_device = None
_keyboard_lock = threading.Lock()

# ── Palette ───────────────────────────────────────────────────────────────────
from led_colors import COLORS, resolve_color

# ── Couleur partagée pour synchronisation avec visualiseur ───────────────────────
import threading
_fan_color_lock = threading.Lock()
_current_fan_color = (255, 105, 180)  # Couleur actuelle des fans (RGB 0-255)
_fan_mix_ratio = 0.0  # Ratio de mix 0-1 (pour debug)

# ── State intelligent pour beat_pulse_dual ─────────────────────────────────────
class BeatPulseDualState:
    """État partagé pour le beat_pulse_dual intelligent."""
    __slots__ = (
        'beat_count', 'last_beat_seq', 'last_beat_time',
        'last_colors'
    )

    def __init__(self):
        self.beat_count = 0
        self.last_beat_seq = -1
        self.last_beat_time = 0.0
        self.last_colors = None  # Pour détecter les changements de couleurs

    def should_reset(self, col1, col2):
        """Vérifie si les couleurs ont changé et réinitialise si nécessaire."""
        current_colors = (col1, col2)
        if self.last_colors != current_colors:
            self.last_colors = current_colors
            self.beat_count = 0  # Reset pour synchroniser l'alternance
            return True
        return False


_dual_state = BeatPulseDualState()


def _get_current_fan_color() -> tuple:
    """Retourne la couleur actuelle des fans pour synchronisation."""
    with _fan_color_lock:
        return _current_fan_color

# ── Config runtime ────────────────────────────────────────────────────────────
_CFG_FILE = _HERE / "led_config.json"

_CFG_DEFAULTS = {
    "rhythm_mode": "impacts",
    "rhythm_sensitivity": 1.0,
    "rhythm_intensity": 1.0,
    "rhythm_release": 0.09,
    "fan_rhythm": "kick",
    "case_rhythm": "snare",
    "keyboard_rhythm": "high",
    "case_mode":     "beat",
    "fan_mode":      "static",      # → beat_pulse_dual auto quand musique détectée
    "keyboard_mode": "sync_fans",  # couleur des fans, sans leur pulsation
    "case_color":    "violet",
    "case_color2":   "cyan",
    "fan_color":     "violet",
    "fan_color2":    "cyan",        # utilisée pour les highs en beat_pulse_dual
    "keyboard_color": "violet",
    "keyboard_color2": "cyan",
    "case_speed":    1.0,
    "fan_speed":     1.0,
    "keyboard_speed": 1.0,
    "case_length":   1.0,
    "brightness":    1.0,
    "case_decay":    0.45,
    "keyboard_enabled": True,
    "keyboard_min_brightness": 0.30,
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

# ── Debounce pour l'écriture de la config ───────────────────────────────────────
_cfg_dirty = False
_cfg_timer = None
_cfg_timer_lock = threading.Lock()
_DEBOUNCE_DELAY = 2.0  # secondes

def _flush_config():
    """Écrit la config sur disque (appelé par le timer)."""
    global _cfg_dirty, _cfg_timer
    with _cfg_lock:
        with _cfg_timer_lock:
            _cfg_timer = None
            if _cfg_dirty:
                _save_cfg(dict(_cfg))
                _cfg_dirty = False

def _schedule_flush():
    """Programme l'écriture de la config avec debounce."""
    global _cfg_timer
    with _cfg_timer_lock:
        # Annuler le timer existant
        if _cfg_timer is not None:
            _cfg_timer.cancel()
        # Programmer un nouveau timer
        _cfg_timer = threading.Timer(_DEBOUNCE_DELAY, _flush_config)
        _cfg_timer.daemon = True  # Ne bloque pas l'arrêt du programme
        _cfg_timer.start()

def get_cfg() -> dict:
    with _cfg_lock:
        return dict(_cfg)

def set_cfg(**kwargs):
    global _cfg_dirty
    with _cfg_lock:
        for k, v in kwargs.items():
            if k in _cfg:
                _cfg[k] = v
        # Marquer dirty et programmer l'écriture
        _cfg_dirty = True
        _schedule_flush()


# ── OpenRGB singleton ─────────────────────────────────────────────────────────
def _connect():
    """Connecte au device principal (case/fans)."""
    try:
        import led_wave as _lw
        if not _lw._ensure_server():
            return False, None
        device = _lw._device
        saved = json.loads((_HERE / "led_map.json").read_text(encoding="utf-8"))
        zones = saved.get("zones", [])
        if zones:
            if saved.get("device") != device.name or [z.name for z in device.zones] != [z["name"] for z in zones]:
                print("[LED] Peripherique different du mapping — refaire map_leds.py")
                return False, None
            for index, config in enumerate(zones):
                zone = device.zones[index]
                if len(zone.leds) != config["size"]:
                    if not zone.name.upper().startswith("D_LED"):
                        print("[LED] Zone fixe incompatible avec le mapping")
                        return False, None
                    zone.resize(config["size"])
                    time.sleep(0.1)
                    device.update()
                    if len(device.zones[index].leds) != config["size"]:
                        print("[LED] Taille ARGB refusee par OpenRGB")
                        return False, None
        # Resizing ARGB outputs can reset the hardware effect. Reclaim direct
        # control after restoring all sizes, including on subsequent retries.
        device.set_custom_mode()
        print(f"[LED] Device principal: {device.name}  —  zones: {[z.name for z in device.zones]}")
        return True, device
    except Exception as e:
        print(f"[LED] connexion device principal échouée: {e}")
        return False, None


def _select_keyboard(devices, expected_name=None):
    if expected_name:
        match = next((d for d in devices if d.name == expected_name), None)
        if match is not None:
            return match
    candidates = [d for d in devices if "G915" in d.name.upper()]
    if len(candidates) == 1:
        return candidates[0]
    candidates = [d for d in devices if "KEYBOARD" in d.name.upper()]
    return candidates[0] if len(candidates) == 1 else None


def _prepare_keyboard(kb):
    """Select the SDK's per-LED mode and restore its hardware brightness."""
    print(f"[LED] Modes clavier: {[m.name for m in kb.modes]}")
    kb.set_custom_mode()
    time.sleep(0.1)
    mode = kb.modes[kb.active_mode]
    maximum = getattr(mode, "brightness_max", None)
    minimum = getattr(mode, "brightness_min", None)
    if isinstance(maximum, (int, float)) and isinstance(minimum, (int, float)) and maximum > minimum:
        mode.brightness = maximum
    # Re-send even if the SDK already reports Direct; hardware may have reset.
    kb.set_mode(mode, force=True)
    time.sleep(0.1)
    print(f"[LED] Mode clavier: {mode.name}, luminosite={getattr(mode, 'brightness', 'n/a')}")


def _connect_keyboard():
    """Connecte au clavier RGB (device séparé)."""
    global _keyboard_device
    with _keyboard_lock:
        if _keyboard_device is not None:
            return True, _keyboard_device

        try:
            import led_wave as _lw
            if not _lw._ensure_server():
                return False, None

            # Le clavier est un device séparé dans le client OpenRGB
            from openrgb import OpenRGBClient
            client = OpenRGBClient()

            # Les indices changent quand OpenRGB détecte de nouveaux périphériques.
            kb_cfg = json.loads((_HERE / "led_map.json").read_text()).get("keyboard", {})
            expected_name = kb_cfg.get("name")
            kb = _select_keyboard(client.devices, expected_name)
            if kb is not None:
                _prepare_keyboard(kb)

                _keyboard_device = kb
                print(f"[LED] Clavier connecté: {kb.name} ({len(kb.leds)} LEDs)")
                return True, _keyboard_device
            else:
                print(f"[LED] Clavier configuré introuvable ({expected_name}) — clavier désactivé")
                return False, None
        except Exception as e:
            print(f"[LED] connexion clavier échouée: {e}")
            return False, None


def _ensure_keyboard():
    """S'assure que le clavier est connecté (réessaye si nécessaire)."""
    global _keyboard_device
    if _keyboard_device is not None:
        return True, _keyboard_device

    # Tenter de reconnecter
    return _connect_keyboard()


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

def _valorant_fullscreen_active():
    """Only override LEDs while Valorant is foreground and fills its monitor."""
    import ctypes
    from ctypes import wintypes
    import psutil

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    window = user32.GetForegroundWindow()
    if not window:
        return False
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(window, ctypes.byref(pid)):
        return False
    try:
        name = psutil.Process(pid.value).name().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    if name not in {"valorant.exe", "valorant-win64-shipping.exe"}:
        return False
    rect = wintypes.RECT()
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromWindow(window, 2)  # nearest monitor
    if not monitor or not user32.GetWindowRect(window, ctypes.byref(rect)):
        return False
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    bounds = info.rcMonitor
    # Allow invisible window borders; works for exclusive and borderless fullscreen.
    return all(abs(a - b) <= 8 for a, b in zip(
        (rect.left, rect.top, rect.right, rect.bottom),
        (bounds.left, bounds.top, bounds.right, bounds.bottom)))

def run(stop_event: threading.Event, get_colors=None):
    global last_progress, _keyboard_device
    import traceback
    print(f"[LED] run() START: stop_event={stop_event.is_set()}")
    while not stop_event.is_set():
        last_progress = time.monotonic()
        try:
            print(f"[LED] run() calling _run_inner()")
            _run_inner(stop_event)
            # Si _run_inner retourne sans exception (e.g. connexion échouée),
            # attendre avant de réessayer pour éviter une boucle serrée
            stop_event.wait(2)
        except Exception:
            traceback.print_exc()
            # Discard stale devices so the next attempt opens a fresh connection.
            import led_wave
            led_wave._device = None
            _keyboard_device = None
            stop_event.wait(2)
    print(f"[LED] run() END: stop_event={stop_event.is_set()}")


def _run_inner(stop_event):
    global last_progress, _keyboard_device
    print(f"[LED] _run_inner START: stop_event={stop_event.is_set()}")
    ok, device = _connect()
    print(f"[LED] _connect returned: ok={ok}")
    if not ok:
        print(f"[LED] _connect FAILED, returning")
        return

    # Connecter le clavier si enabled
    kb_ok, keyboard = _connect_keyboard() if get_cfg().get("keyboard_enabled", True) else (False, None)

    total_leds  = len(device.leds)
    fan_idxs, case_idxs = _mapped_indices(device)
    main_enabled = bool(fan_idxs or case_idxs)
    if not main_enabled and not keyboard:
        stop_event.wait(2)
        return
    all_colors  = [RGBColor(0, 0, 0)] * total_leds
    kb_leds = len(keyboard.leds) if keyboard else 0
    kb_colors   = [RGBColor(0, 0, 0)] * kb_leds if keyboard else None

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

    # Auto mode: détecte la musique et passe les fans en beat_pulse
    last_fan_mode = "spin"
    original_fan_mode = last_fan_mode
    music_detected = False
    music_silence_time = 0.0
    MUSIC_THRESHOLD = 0.15   # niveau audio pour détecter la musique
    SILENCE_DELAY = 2.0       # secondes de silence avant de quitter beat_pulse

    import colorsys

    print(f"[LED] fans={fan_idxs}  case={case_idxs}  total={total_leds}")
    if keyboard:
        print(f"[LED] Clavier actif: {keyboard.name} ({kb_leds} LEDs)")

    # Debug timer (affiche le niveau audio toutes les 5s)
    _debug_last_print = 0.0
    _keyboard_error_t = float('-inf')
    keyboard_retry_at = time.monotonic() + 5.0

    pulse_level = 0.0
    pulse_mix = 0.0
    previous_frame = time.monotonic() - FRAME_TIME
    valorant_active = False
    game_check_at = 0.0

    while not stop_event.is_set():
        now = time.monotonic()
        if now >= game_check_at:
            try:
                valorant_active = _valorant_fullscreen_active()
            except Exception:
                valorant_active = False
            game_check_at = now + 0.5
        last_progress = now
        dt = max(0.0, now - previous_frame)
        previous_frame = now
        try:
            import bpm_source as _bs
            target_level = min(1.0, max(0.0, _bs.get_level()))
        except Exception:
            target_level = 0.0
        # Time-based smoothing keeps the attack responsive and the tail soft.
        pulse_level = _pulse_envelope(pulse_level, target_level, dt)

        cfg = get_cfg()
        rhythm_levels = {}
        try:
            _bs.configure_rhythm(cfg["rhythm_sensitivity"])
            rhythm_levels = _bs.get_rhythm_levels(max(0.02, min(0.5, cfg["rhythm_release"])))
        except Exception:
            pass
        def rhythm_level(zone):
            source = cfg.get(zone + "_rhythm", "kick") if cfg["rhythm_mode"] == "split" else "kick"
            return min(1.0, rhythm_levels.get(source, 0.0) * max(0.0, cfg["rhythm_intensity"]))

        # A missing keyboard must not restart the motherboard animation.
        if keyboard is None and cfg.get("keyboard_enabled", True) and now >= keyboard_retry_at:
            kb_ok, keyboard = _connect_keyboard()
            kb_leds = len(keyboard.leds) if keyboard else 0
            kb_colors = [RGBColor(0, 0, 0)] * kb_leds if keyboard else None
            keyboard_retry_at = time.monotonic() + 5.0

        bri        = cfg["brightness"]
        case_spd   = cfg["case_speed"]
        fan_spd    = cfg["fan_speed"]
        n_case     = max(1, int(len(case_idxs) * cfg["case_length"]))
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
            return resolve_color(name)
        def lerp(c1, c2, t):
            return (int(c1[0]+(c2[0]-c1[0])*t), int(c1[1]+(c2[1]-c1[1])*t), int(c1[2]+(c2[2]-c1[2])*t))
        def mk(r, g, b, intensity=1.0):
            return RGBColor(_clamp(r*bri*intensity), _clamp(g*bri*intensity), _clamp(b*bri*intensity))

        def animate_zone(mode, idxs, col1, col2, speed, n_lit, beat_b):
            """Apply one mode to a list of LED indices. Returns updated offsets via side-effects on shared state."""
            nonlocal angle, wave_off, hue_off, breathe_t, pulse_mix
            N = len(idxs)
            if not N:
                return
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
                # (bpm_source.get_level() = envelope follower asymétrique, attack ~12ms / decay ~80ms,
                #  normalisé sur percentile 95 glissant — identique au meilleur algo du visualiseur HTML)
                try:
                    import bpm_source as _bs
                    level = pulse_level
                except Exception:
                    level = 0.0
                for idx in active:
                    all_colors[ZONE_OFFSET + idx] = mk(col1[0], col1[1], col1[2], level)

            elif mode == "beat_pulse_dual":
                # ═══════════════════════════════════════════════════════════════
                # BEAT PULSE DUAL - ALTERNANCE RAPIDE
                # • Beat 1 → col1, Beat 2 → col2, Beat 3 → col1, etc.
                # • Intensité basée sur l'énergie audio
                # • Fondu court entre les couleurs à chaque beat
                # ═══════════════════════════════════════════════════════════════
                try:
                    import bpm_source as _bs
                    level = pulse_level
                    _, new_beat_seq = _bs.get_beat_event()
                except Exception:
                    level = 0.0
                    new_beat_seq = _dual_state.last_beat_seq

                # Reset si les couleurs ont changé
                _dual_state.should_reset(col1, col2)

                # Détection de nouveau beat
                if new_beat_seq != _dual_state.last_beat_seq:
                    _dual_state.last_beat_seq = new_beat_seq
                    _dual_state.beat_count += 1
                    _dual_state.last_beat_time = now



                # Alternance de couleur sur chaque beat
                # beat_count pair → col1, beat_count impair → col2
                use_col2 = (_dual_state.beat_count % 2) == 1
                target_mix = float(use_col2)
                pulse_mix += (target_mix - pulse_mix) * (-math.expm1(-dt / 0.110))

                # Intensité basée sur le niveau audio
                intensity = level

                # LED éteintes si silence
                if intensity < 0.03:
                    current_color = (0, 0, 0)
                    for idx in active:
                        all_colors[ZONE_OFFSET + idx] = RGBColor(0, 0, 0)
                else:
                    # Couleur actuelle selon alternance
                    current_color = lerp(col1, col2, pulse_mix)
                    for idx in active:
                        all_colors[ZONE_OFFSET + idx] = mk(*current_color, intensity)

                # Partage la couleur avec le visualiseur
                with _fan_color_lock:
                    global _current_fan_color, _fan_mix_ratio
                    _current_fan_color = current_color
                    _fan_mix_ratio = pulse_mix

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

        # ── Détection musique → auto beat_pulse sur les fans ────────────────
        try:
            import bpm_source as _bs
            current_level = _bs.get_level()
            current_bpm = _bs.get_bpm()

            # Musique détectée si BPM valide OU niveau audio élevé
            is_music = (current_bpm is not None) or (current_level > MUSIC_THRESHOLD)

            if is_music:
                if not music_detected:
                    # Début de la musique → passe en beat_pulse
                    music_detected = True
                    original_fan_mode = cfg.get("fan_mode", "spin")
                    print(f"[LED] Musique détectée (mode configuré: {original_fan_mode})")
                music_silence_time = 0.0
            else:
                if music_detected:
                    music_silence_time += FRAME_TIME
                    # Après SILENCE_DELAY secondes de silence, revient au mode original
                    if music_silence_time >= SILENCE_DELAY:
                        music_detected = False
                        print(f"[LED] Silence détecté → fans {original_fan_mode}")
                else:
                    music_silence_time = 0.0

            # Respecte les modes pulse explicites et off, même avec de la musique.
            active_fan_mode = cfg.get("fan_mode", "spin")
            if music_detected and active_fan_mode not in ("off", "beat_pulse", "beat_pulse_dual"):
                active_fan_mode = "beat_pulse_dual"
        except Exception:
            active_fan_mode = cfg.get("fan_mode", "spin")

        # ── Fans ─────────────────────────────────────────────────────────
        animate_zone(active_fan_mode, fan_idxs,
                     c(cfg["fan_color"]), c(cfg["fan_color2"]),
                     fan_spd, len(fan_idxs), beat_bright)

        # ── Case ─────────────────────────────────────────────────────────
        animate_zone(cfg["case_mode"], case_idxs,
                     c(cfg["case_color"]), c(cfg["case_color2"]),
                     case_spd, n_case, beat_bright)

        # ── Clavier ───────────────────────────────────────────────────────────
        if keyboard and cfg.get("keyboard_enabled", True):
            kb_mode = cfg.get("keyboard_mode", "sync_fans")
            kb_spd = cfg.get("keyboard_speed", 1.0)

            if kb_mode == "sync_fans":
                # Follow the fan hue only; never copy the animated brightness.
                col = c(cfg["fan_color"])
                if active_fan_mode == "beat_pulse_dual":
                    col = lerp(col, c(cfg["fan_color2"]), pulse_mix)
                for kb_i in range(kb_leds):
                    kb_colors[kb_i] = mk(*col)

            elif kb_mode == "off":
                for kb_i in range(kb_leds):
                    kb_colors[kb_i] = RGBColor(0, 0, 0)

            elif kb_mode == "static":
                col = c(cfg.get("keyboard_color", "violet"))
                for kb_i in range(kb_leds):
                    kb_colors[kb_i] = mk(*col)

            elif kb_mode == "beat_pulse":
                # Beat pulse sur tout le clavier
                try:
                    import bpm_source as _bs
                    level = pulse_level
                except Exception:
                    level = 0.0
                col = c(cfg.get("keyboard_color", "violet"))
                for kb_i in range(kb_leds):
                    kb_colors[kb_i] = mk(col[0] * level, col[1] * level, col[2] * level)

            elif kb_mode == "wave":
                col1 = c(cfg.get("keyboard_color", "violet"))
                col2 = c(cfg.get("keyboard_color2", "cyan"))
                wave_off = math.fmod(wave_off + 0.6 * kb_spd, kb_leds * 2)
                cycle = kb_leds * 2
                for kb_i in range(kb_leds):
                    t = ((kb_i + int(wave_off)) % cycle) / cycle
                    b = 0.3 + 0.7 * abs(math.sin(t * math.pi))
                    kb_colors[kb_i] = mk(col1[0]*b, col1[1]*b, col1[2]*b)

        # Apply causal impacts after legacy rendering; explicit off remains respected.
        if cfg["rhythm_mode"] in ("impacts", "split"):
            for zone, indices in (("fan", fan_idxs), ("case", case_idxs)):
                if cfg[zone + "_mode"] != "off":
                    for idx in indices:
                        all_colors[ZONE_OFFSET + idx] = mk(*c(cfg[zone + "_color"]), rhythm_level(zone))
            if keyboard and cfg["keyboard_mode"] != "off":
                kb_colors = [mk(*c(cfg["keyboard_color"]), rhythm_level("keyboard"))] * kb_leds

        # Independent of global brightness: preserve readable keys even in breaks.
        if keyboard and cfg.get("keyboard_enabled", True) and cfg["keyboard_mode"] != "off":
            floor = min(1.0, max(0.0, float(cfg["keyboard_min_brightness"])))
            base = c(cfg["keyboard_color"])
            kb_colors = [RGBColor(*[_clamp(base[i] * floor + channel * (1 - floor))
                                    for i, channel in enumerate((color.red, color.green, color.blue))])
                         for color in kb_colors]

        # Temporary game override, after audio effects; never persist it to config.
        if valorant_active:
            for idx in fan_idxs + case_idxs:
                all_colors[ZONE_OFFSET + idx] = mk(255, 0, 0)

        send_started = time.monotonic()
        error = _send_frame(
            device if main_enabled else None, all_colors,
            keyboard if cfg.get("keyboard_enabled", True) else None, kb_colors)
        _led_timing.update(send_ms=(time.monotonic()-send_started)*1000,
                           frame_ms=(time.monotonic()-now)*1000)
        if error is not None:
            if now - _keyboard_error_t >= 5:
                print(f"[LED] Envoi clavier echoue: {type(error).__name__}: {error}; nouvel essai dans 5s")
                _keyboard_error_t = now
            keyboard = None
            _keyboard_device = None
            keyboard_retry_at = time.monotonic() + 5.0
        stop_event.wait(max(0.0, FRAME_TIME - (time.monotonic() - now)))


def shutdown():
    import led_wave as _lw
    _lw.shutdown()
