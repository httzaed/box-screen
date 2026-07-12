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
  sync_fans      : le clavier mirror exactement les fans (mêmes couleurs, même timing)
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
FAN_LEDS  = len(FAN_IDXS)   # 15 — position 0-14 = 1 LED sur chaque fan simultanément
FRAME_TIME = 0.020           # ~50 fps

# ── Clavier OpenRGB ─────────────────────────────────────────────────────────────
_keyboard_device = None
_keyboard_lock = threading.Lock()

# ── Palette ───────────────────────────────────────────────────────────────────
COLORS = {
    "violet": (180,   0, 255),
    "cyan":   (  0, 200, 255),
    "blue":   (  0,  80, 255),
    "teal":   (  0, 210, 150),
    "green":  (  0, 255, 100),
    "yellow": (255, 200,   0),
    "orange": (255,  40,   0),
    "red":    (255,   0,   0),
    "pink":   (255,  20, 100),
    "white":  (255, 255, 255),
}

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
    "case_mode":     "beat",
    "fan_mode":      "static",      # → beat_pulse_dual auto quand musique détectée
    "keyboard_mode": "sync_fans",  # le clavier sync par défaut avec les fans
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
        print(f"[LED] Device principal: {device.name}  —  zones: {[z.name for z in device.zones]}")
        return True, device
    except Exception as e:
        print(f"[LED] connexion device principal échouée: {e}")
        return False, None


def _connect_keyboard():
    """Connecte au clavier RGB (device séparé)."""
    global _keyboard_device
    with _keyboard_lock:
        if _keyboard_device is not None:
            return True, _keyboard_device

        if KEYBOARD_DEVICE_IDX is None:
            print("[LED] KEYBOARD_DEVICE_IDX non défini — clavier désactivé")
            return False, None

        try:
            import led_wave as _lw
            if not _lw._ensure_server():
                return False, None

            # Le clavier est un device séparé dans le client OpenRGB
            from openrgb import OpenRGBClient
            client = OpenRGBClient()

            # Récupère le device par son index
            if KEYBOARD_DEVICE_IDX < len(client.devices):
                kb = client.devices[KEYBOARD_DEVICE_IDX]
                # set_mode "direct" pour le contrôle LED
                try:
                    kb.set_mode("direct")
                except Exception:
                    try:
                        kb.set_mode("Direct")
                    except Exception:
                        pass  # Certains claviers n'ont pas de mode direct

                _keyboard_device = kb
                print(f"[LED] Clavier connecté: {kb.name} ({len(kb.leds)} LEDs)")
                return True, _keyboard_device
            else:
                print(f"[LED] Device index {KEYBOARD_DEVICE_IDX} invalide — clavier désactivé")
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

def run(stop_event: threading.Event, get_colors=None):
    import traceback
    print(f"[LED] run() START: stop_event={stop_event.is_set()}")
    while not stop_event.is_set():
        try:
            print(f"[LED] run() calling _run_inner()")
            _run_inner(stop_event)
            # Si _run_inner retourne sans exception (e.g. connexion échouée),
            # attendre avant de réessayer pour éviter une boucle serrée
            time.sleep(2)
        except Exception:
            traceback.print_exc()
            time.sleep(1)
    print(f"[LED] run() END: stop_event={stop_event.is_set()}")


def _run_inner(stop_event):
    print(f"[LED] _run_inner START: stop_event={stop_event.is_set()}")
    ok, device = _connect()
    print(f"[LED] _connect returned: ok={ok}")
    if not ok:
        print(f"[LED] _connect FAILED, returning")
        return

    # Connecter le clavier si enabled
    kb_ok, keyboard = _connect_keyboard() if _CFG_DEFAULTS.get("keyboard_enabled", True) else (False, None)

    total_leds  = len(device.leds)
    all_colors  = [RGBColor(0, 0, 0)] * total_leds
    kb_colors   = [RGBColor(0, 0, 0)] * KEYBOARD_LEDS if keyboard else None

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

    print(f"[LED] fans={FAN_IDXS}  case={CASE_IDXS[0]}..{CASE_IDXS[-1]}  total={total_leds}")
    if keyboard:
        print(f"[LED] Clavier actif: {keyboard.name} ({KEYBOARD_LEDS} LEDs)")

    # Debug timer (affiche le niveau audio toutes les 5s)
    _debug_last_print = 0.0

    while not stop_event.is_set():
        now = time.monotonic()

        # Debug output for troubleshooting (first thing in loop)
        if now % 1.0 < 0.05:  # Print every second
            print(f"[LED] LOOP RUNNING: now={now:.1f}")

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
                    # Minimum 10% brightness to ensure LEDs are visible
                    level = max(level, 0.1)
                except Exception:
                    level = 0.5  # Fallback to 50% brightness if BPM source fails
                for idx in active:
                    all_colors[ZONE_OFFSET + idx] = mk(col1[0], col1[1], col1[2], level)

            elif mode == "beat_pulse_dual":
                # ═══════════════════════════════════════════════════════════════
                # BEAT PULSE DUAL - ALTERNANCE RAPIDE
                # • Beat 1 → col1, Beat 2 → col2, Beat 3 → col1, etc.
                # • Intensité basée sur l'énergie audio
                # • Transition instantanée sur chaque beat détecté
                # ═══════════════════════════════════════════════════════════════
                try:
                    import bpm_source as _bs
                    level = _bs.get_level()
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
                    beat_detected = True
                else:
                    beat_detected = False

                # Alternance de couleur sur chaque beat
                # beat_count pair → col1, beat_count impair → col2
                use_col2 = (_dual_state.beat_count % 2) == 1

                # Intensité basée sur le niveau audio
                intensity = level
                if beat_detected:
                    intensity = min(1.0, intensity * 1.3)  # Boost sur beat
                intensity = max(intensity, 0.05)  # Minimum visible

                # LED éteintes si silence
                if intensity < 0.03:
                    current_color = (0, 0, 0)
                    for idx in active:
                        all_colors[ZONE_OFFSET + idx] = RGBColor(0, 0, 0)
                else:
                    # Couleur actuelle selon alternance
                    current_color = col2 if use_col2 else col1
                    for idx in active:
                        all_colors[ZONE_OFFSET + idx] = mk(*current_color, intensity)

                # Partage la couleur avec le visualiseur
                with _fan_color_lock:
                    global _current_fan_color, _fan_mix_ratio
                    _current_fan_color = current_color
                    _fan_mix_ratio = 1.0 if use_col2 else 0.0

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
                    print(f"[LED] Musique détectée → fans beat_pulse_dual (mode original: {original_fan_mode})")
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

            # Utilise beat_pulse_dual si musique détectée, sinon le mode configuré
            active_fan_mode = "beat_pulse_dual" if music_detected else cfg.get("fan_mode", "spin")
        except Exception:
            active_fan_mode = cfg.get("fan_mode", "spin")

        # ── Fans ─────────────────────────────────────────────────────────
        animate_zone(active_fan_mode, FAN_IDXS,
                     c(cfg["fan_color"]), c(cfg["fan_color2"]),
                     fan_spd, len(FAN_IDXS), beat_bright)

        # ── Case ─────────────────────────────────────────────────────────
        animate_zone(cfg["case_mode"], CASE_IDXS,
                     c(cfg["case_color"]), c(cfg["case_color2"]),
                     case_spd, n_case, beat_bright)

        # ── Clavier ───────────────────────────────────────────────────────────
        if keyboard and cfg.get("keyboard_enabled", True):
            kb_mode = cfg.get("keyboard_mode", "sync_fans")
            kb_spd = cfg.get("keyboard_speed", 1.0)

            if kb_mode == "sync_fans":
                # Le clavier mirror exactement les fans (couleurs + timing)
                # Répète le pattern des fans sur tout le clavier
                for kb_i in range(KEYBOARD_LEDS):
                    # Répéter le pattern des 15 fans sur les 117 LEDs du clavier
                    fan_idx = FAN_IDXS[kb_i % len(FAN_IDXS)]
                    kb_colors[kb_i] = all_colors[ZONE_OFFSET + fan_idx]

            elif kb_mode == "off":
                for kb_i in range(KEYBOARD_LEDS):
                    kb_colors[kb_i] = RGBColor(0, 0, 0)

            elif kb_mode == "static":
                col = c(cfg.get("keyboard_color", "violet"))
                for kb_i in range(KEYBOARD_LEDS):
                    kb_colors[kb_i] = mk(*col)

            elif kb_mode == "beat_pulse":
                # Beat pulse sur tout le clavier
                try:
                    import bpm_source as _bs
                    level = _bs.get_level()
                except Exception:
                    level = 0.0
                col = c(cfg.get("keyboard_color", "violet"))
                # Baisser le seuil pour que ça s'allume même avec un faible niveau
                level = max(level, 0.05)  # Minimum 5% pour voir quelque chose
                for kb_i in range(KEYBOARD_LEDS):
                    kb_colors[kb_i] = mk(col[0] * level, col[1] * level, col[2] * level)

            elif kb_mode == "wave":
                col1 = c(cfg.get("keyboard_color", "violet"))
                col2 = c(cfg.get("keyboard_color2", "cyan"))
                wave_off = math.fmod(wave_off + 0.6 * kb_spd, KEYBOARD_LEDS * 2)
                cycle = KEYBOARD_LEDS * 2
                for kb_i in range(KEYBOARD_LEDS):
                    t = ((kb_i + int(wave_off)) % cycle) / cycle
                    b = 0.3 + 0.7 * abs(math.sin(t * math.pi))
                    kb_colors[kb_i] = mk(col1[0]*b, col1[1]*b, col1[2]*b)

            try:
                keyboard.set_colors(kb_colors)
            except Exception:
                pass  # Le clavier peut être déconnecté

        device.set_colors(all_colors)
        time.sleep(FRAME_TIME)


def shutdown():
    import led_wave as _lw
    _lw.shutdown()
