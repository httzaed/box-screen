"""
Panel launcher — switche entre les modes avec Ctrl+Shift+Fleche
  Ctrl+Shift+Droite : mode suivant   (ascii_vhs -> video -> image)
  Ctrl+Shift+Gauche : mode précédent
  Ctrl+Shift+Haut   : HUD style suivant   (full -> terminal -> clock -> split -> tiles -> matrix)
  Ctrl+Shift+Bas    : HUD style précédent

Modes    (contenu/fond) : ascii_vhs, video, image
HUD styles (rendu HUD)  : full, terminal, clock, split, tiles, matrix

  --auto  : Mode auto-intelligent (détecte vidéo/musique/lyrics et adapte l'affichage)
"""
import time, math, random, importlib, threading, io, struct
try:
    import keyboard as _keyboard
    _KEYBOARD_LIB = True
except ImportError:
    _KEYBOARD_LIB = False
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

import sys as _sys
import json as _json

import render
import render_tiles
import render_matrix
import render_audio_viz
from panel import Panel, WIDTH, HEIGHT, _image_to_jpeg, _send_frame
import control_server

# ── LED (wave + fans) ───────────────────────────────────────────────────────
_sys.path.insert(0, str(Path(__file__).parent.parent / "LEDs"))
try:
    import led_fans as _led   # gère vague case + rotation fans
    _LED = True
except ImportError:
    try:
        import led_wave as _led   # fallback sans fans
        _LED = True
    except ImportError:
        _led = None
        _LED = False
        print("[LED] led_fans/led_wave introuvables — LEDs désactivées")

# ── Config ─────────────────────────────────────────────────────────────────
_HERE       = Path(__file__).parent
VIDEO_FILE  = str(next(_HERE.glob("*.mp4"),  _HERE / "video.mp4"))
IMAGE_FILE  = str(next(_HERE.glob("*.png"),  _HERE / "image.png"))
ASCII_FILE  = _HERE / "ascii_bg.txt"

MODES       = ["ascii_vhs", "video", "image", "audio", "blank"]
FPS_VIDEO   = 25
FPS_ASCII   = 14
FPS_IMAGE   = 1

# HUD styles : les 4 layouts render.py + tiles + matrix + audio_viz (renderers autonomes)
HUD_STYLES  = ["full", "terminal", "clock", "split", "tiles", "matrix", "lyrics", "audio_viz", "blank"]

# ── ASCII VHS config ───────────────────────────────────────────────────────
ASCII_START = (221,   0, 255)   # violet/rose vif — identique à st_wave.py
ASCII_END   = (255,  40,   0)   # orange-rouge pétant — identique à st_wave.py
ASCII_BG    = (6, 4, 12)
ASCII_SIZE  = 13
INTENSITY   = 0.4

# ── LED couleurs par mode ───────────────────────────────────────────────────
_LED_COLORS = {
    "ascii_vhs": (ASCII_START, ASCII_END),         # violet -> orange
    "image":     ((0, 162, 216), (0, 216, 54)),    # cyan ciel -> vert jungle
    "video":     ((255, 20, 100), (50, 100, 220)), # rose vif -> bleu acier
    "audio":     (ASCII_END, ASCII_END),            # orange rougeâtre (ASCII_END)
    "blank":     ((0, 0, 0), (0, 0, 0)),           # LEDs éteintes
}

def _palette_colors():
    """Lit palette.json (écrit par video.py --palette) pour coller aux couleurs vidéo."""
    try:
        p  = _json.loads((_HERE / "palette.json").read_text())
        c1 = tuple(p["accent"])
        c2 = tuple(p["text"][0]) if p.get("text") else (200, 200, 200)
        return c1, c2
    except Exception:
        return (0xFF, 0x35, 0x00), (0xDD, 0x00, 0xFF)

# ── Persistence mode/hud ───────────────────────────────────────────────────
_STATE_FILE = _HERE / "last_state.json"

def _load_state():
    try:
        d = _json.loads(_STATE_FILE.read_text())
        mi = MODES.index(d["mode"]) if d.get("mode") in MODES else 0
        hi = HUD_STYLES.index(d["hud"]) if d.get("hud") in HUD_STYLES else 0
        return mi, hi
    except Exception:
        return 0, 0

def _save_state():
    try:
        _STATE_FILE.write_text(_json.dumps({
            "mode": MODES[_mode_idx % len(MODES)],
            "hud":  HUD_STYLES[_hud_idx % len(HUD_STYLES)],
        }))
    except Exception:
        pass

# ── State ──────────────────────────────────────────────────────────────────
_mode_idx, _hud_idx = _load_state()
# Cascade only : l'app boote toujours sur audio+lyrics (la cascade fait le reste)
_mode_idx = MODES.index("audio")
_hud_idx  = HUD_STYLES.index("lyrics")
_mode_lock  = threading.Lock()
_stop_event = threading.Event()
_panel      = None
_led_stop   = None
_led_thread = None

# ── Auto mode ───────────────────────────────────────────────────────────────
_auto_mode      = False
_auto_enabled   = False  # True quand --auto est passé
_last_context   = None
_auto_check_interval = 2.0
_last_auto_check = 0.0
_last_applied_mode = None   # Pour éviter de reapply le même mode
_last_applied_led = None    # Pour éviter de reapply la même config LED


# ══════════════════════════════════════════════════════════════════════════
# Helpers communs
# ══════════════════════════════════════════════════════════════════════════

def _fit_crop(img: Image.Image) -> Image.Image:
    src_ratio = img.width / img.height
    dst_ratio = WIDTH / HEIGHT
    if src_ratio > dst_ratio:
        new_h = HEIGHT; new_w = int(HEIGHT * src_ratio)
    else:
        new_w = WIDTH;  new_h = int(WIDTH / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    x = (new_w - WIDTH) // 2; y = (new_h - HEIGHT) // 2
    return img.crop((x, y, x + WIDTH, y + HEIGHT))

def _send(img: Image.Image):
    """Envoie une frame en appliquant le HUD actif."""
    try:
        # Obtenir le HUD actuel de manière thread-safe
        with _mode_lock:
            hud = HUD_STYLES[_hud_idx]

        if hud == "tiles":
            _panel.send_image(render_tiles.build_frame(bg=img), fit=False)
        elif hud == "matrix":
            _panel.send_image(render_matrix.build_frame(bg=img), fit=False)
        elif hud == "audio_viz":
            _panel.send_image(render_audio_viz.build_frame(bg=img), fit=False)
        elif hud == "blank":
            render.LAYOUT = "full"
            render.background_override = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
            _panel.send_image(render.build_frame(), fit=False)
        else:
            render.LAYOUT = hud
            render.background_override = img
            _panel.send_image(render.build_frame(), fit=False)
    except Exception as e:
        print(f"[SEND] Erreur envoi frame: {e}")
        # Ne pas crasher, juste logger l'erreur

def _mono(size):
    candidates = [
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "C:/Windows/Fonts/lucon.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/courier-prime/CourierPrime-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


# ══════════════════════════════════════════════════════════════════════════
# Mode : ASCII VHS
# ══════════════════════════════════════════════════════════════════════════

def _ascii_vhs_loop(stop):
    import numpy as np

    font  = _mono(ASCII_SIZE)
    dummy = Image.new("RGB", (1,1)); d = ImageDraw.Draw(dummy)
    b     = d.textbbox((0,0), "@", font=font)
    cw, ch = max(1, b[2]-b[0]), max(1, b[3]-b[1]+2)

    raw_lines = ASCII_FILE.read_text(encoding="utf-8").split("\n")
    cols      = max(len(l) for l in raw_lines)
    base_lines = [l[:cols].ljust(cols) for l in raw_lines]

    # ── Pre-render stamps : un numpy array (ch×cw×3) par caractère unique ──
    unique_chars = set(c for l in base_lines for c in l if c not in (" ", "\r"))
    stamps: dict[str, np.ndarray] = {}
    for c in unique_chars:
        s_img  = Image.new("RGB", (cw, ch), (0, 0, 0))
        ImageDraw.Draw(s_img).text((0, 0), c, font=font, fill=(255, 255, 255))
        stamps[c] = np.array(s_img, dtype=np.float32) / 255.0  # 0..1 mask

    # Gradient horizontal pré-calculé : (cols, 3) float32
    t_arr = np.linspace(0, 1, cols, dtype=np.float32)
    grad  = np.stack([
        ASCII_START[0] + (ASCII_END[0]-ASCII_START[0]) * t_arr,
        ASCII_START[1] + (ASCII_END[1]-ASCII_START[1]) * t_arr,
        ASCII_START[2] + (ASCII_END[2]-ASCII_START[2]) * t_arr,
    ], axis=1).astype(np.float32)  # (cols, 3)

    x_off = (WIDTH  - cw * cols)   // 2
    y_off = (HEIGHT - ch * len(base_lines)) // 2

    def vhs(frame):
        t = frame / 30.0; out = []
        amp = 2 + 6*INTENSITY; freq = 0.18 + 0.25*INTENSITY
        for idx, ln in enumerate(base_lines):
            dx = int(amp * math.sin(t*2*math.pi*freq + idx*0.12))
            s  = (" "*dx + ln) if dx > 0 else ln[abs(dx):]
            s  = s[:cols].ljust(cols)
            arr = list(s)
            for i, c in enumerate(arr):
                if c != " " and random.random() < 0.006 + 0.02*INTENSITY:
                    arr[i] = random.choice(["`", "'", ".", ",", "-"])
            out.append("".join(arr))
        if random.random() < 0.05*INTENSITY:
            sh = random.randint(1, 2); st = random.randint(0, max(0, len(out)-sh))
            dx = random.choice([-6, 6])
            for i in range(st, st+sh):
                s = (" "*dx + out[i]) if dx > 0 else out[i][abs(dx):]
                out[i] = s[:cols].ljust(cols)
        return out

    frame_n = 0
    while not stop.is_set():
        t0 = time.time()
        vl = vhs(frame_n)

        # Canvas numpy
        canvas = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)

        for row, ln in enumerate(vl):
            y = y_off + row * ch
            if y < 0 or y + ch > HEIGHT: continue
            dim = 0.65 if row % 2 == 1 else 1.0
            for col, char in enumerate(ln):
                if char in (" ", "\r"): continue
                if char not in stamps: continue
                x = x_off + col * cw
                if x < 0 or x + cw > WIDTH: continue
                color = grad[col] * dim
                stamp = stamps[char]
                canvas[y:y+ch, x:x+cw] += stamp * color

        np.clip(canvas, 0, 255, out=canvas)
        img = Image.fromarray(canvas.astype(np.uint8))
        _send(img)
        frame_n += 1
        time.sleep(max(0, 1/FPS_ASCII - (time.time()-t0)))


# ══════════════════════════════════════════════════════════════════════════
# Mode : Vidéo
# ══════════════════════════════════════════════════════════════════════════

def _video_loop(stop):
    import cv2
    cap = cv2.VideoCapture(VIDEO_FILE)
    fps = cap.get(cv2.CAP_PROP_FPS) or FPS_VIDEO
    while not stop.is_set():
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0); continue
        t0  = time.time()
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        _send(_fit_crop(img))
        time.sleep(max(0, 1/fps - (time.time()-t0)))
    cap.release()


# ══════════════════════════════════════════════════════════════════════════
# Mode : Image statique
# ══════════════════════════════════════════════════════════════════════════

def _image_loop(stop):
    import datetime as _dt

    bg       = _fit_crop(Image.open(IMAGE_FILE).convert("RGB"))
    last_hud = None
    last_sec = -1
    cached_jpeg = None

    while not stop.is_set():
        t0  = time.time()
        now = _dt.datetime.now()

        # Obtenir le HUD actuel de manière thread-safe
        with _mode_lock:
            hud = HUD_STYLES[_hud_idx]

        # audio_viz et lyrics: toujours refresh (real-time)
        # autres: refresh si seconde écoulée OU si le HUD a changé
        if hud in ("audio_viz", "lyrics") or now.second != last_sec or hud != last_hud:
            last_sec = now.second
            last_hud = hud
            if hud == "tiles":
                cached_jpeg = _image_to_jpeg(render_tiles.build_frame(bg=bg))
            elif hud == "matrix":
                cached_jpeg = _image_to_jpeg(render_matrix.build_frame(bg=bg))
            elif hud == "audio_viz":
                # Real-time, no cache
                cached_jpeg = None
            elif hud == "blank":
                render.LAYOUT = "full"
                render.background_override = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
                cached_jpeg = _image_to_jpeg(render.build_frame())
            else:
                render.LAYOUT = hud
                render.background_override = bg
                cached_jpeg = _image_to_jpeg(render.build_frame())

        # audio_viz: toujours envoyer une frame fraîche
        if hud == "audio_viz":
            _panel.send_image(render_audio_viz.build_frame(bg=bg), fit=False)
        elif cached_jpeg:
            _send_frame(_panel._ep_out, _panel._ep_in, cached_jpeg)

        # fps adaptatif selon le mode
        if hud in ("audio_viz", "lyrics"):
            target_fps = 30
        else:
            target_fps = FPS_IMAGE
        time.sleep(max(0, 1/target_fps - (time.time()-t0)))


# ══════════════════════════════════════════════════════════════════════════
# Mode : Blank (écran noir + HUD uniquement)
# ══════════════════════════════════════════════════════════════════════════

_BLACK_BG = None  # initialisé au premier appel pour avoir WIDTH/HEIGHT dispo

def _blank_loop(stop):
    import datetime as _dt
    global _BLACK_BG
    if _BLACK_BG is None:
        _BLACK_BG = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))

    last_hud = None
    last_sec = -1
    cached_jpeg = None

    while not stop.is_set():
        t0  = time.time()
        now = _dt.datetime.now()

        # Obtenir le HUD actuel de manière thread-safe
        with _mode_lock:
            hud = HUD_STYLES[_hud_idx]

        # audio_viz et lyrics: toujours refresh (real-time)
        # autres: refresh si seconde écoulée OU si le HUD a changé
        if hud in ("audio_viz", "lyrics") or now.second != last_sec or hud != last_hud:
            last_sec = now.second
            last_hud = hud
            if hud == "tiles":
                cached_jpeg = _image_to_jpeg(render_tiles.build_frame(bg=_BLACK_BG))
            elif hud == "matrix":
                cached_jpeg = _image_to_jpeg(render_matrix.build_frame(bg=_BLACK_BG))
            elif hud == "audio_viz":
                # Real-time, no cache
                cached_jpeg = None
            elif hud == "lyrics":
                # Real-time, no cache - lyrics changent en continu
                cached_jpeg = None
            else:
                render.LAYOUT = "full" if hud == "blank" else hud
                render.background_override = _BLACK_BG
                cached_jpeg = _image_to_jpeg(render.build_frame())

        # audio_viz et lyrics: toujours envoyer une frame fraîche (real-time)
        if hud == "audio_viz":
            _panel.send_image(render_audio_viz.build_frame(bg=_BLACK_BG), fit=False)
        elif hud == "lyrics":
            # Lyrics: génère la frame en temps réel avec fond noir
            render.LAYOUT = "lyrics"
            render.background_override = _BLACK_BG
            _panel.send_image(render.build_frame(), fit=False)
        elif cached_jpeg:
            _send_frame(_panel._ep_out, _panel._ep_in, cached_jpeg)

        # fps adaptatif selon le mode
        if hud in ("audio_viz", "lyrics"):
            target_fps = 30
        else:
            target_fps = FPS_IMAGE
        time.sleep(max(0, 1/target_fps - (time.time()-t0)))


# ══════════════════════════════════════════════════════════════════════════
# Mode : Audio Visualizer (direct, no HUD overlay)
# ══════════════════════════════════════════════════════════════════════════

def _audio_loop(stop):
    """Audio mode avec cascade : lyrics -> audio viz -> idle."""
    while not stop.is_set():
        t0 = time.time()
        # Utilise la cascade via render.build_frame() avec LAYOUT="lyrics"
        render.LAYOUT = "lyrics"
        render.background_override = _BLACK_BG
        _panel.send_image(render.build_frame(), fit=False)
        time.sleep(max(0, 1/30 - (time.time()-t0)))  # 30 FPS


# ══════════════════════════════════════════════════════════════════════════
# Switcher
# ══════════════════════════════════════════════════════════════════════════

MODE_FNS = {
    "ascii_vhs": _ascii_vhs_loop,
    "video":     _video_loop,
    "image":     _image_loop,
    "audio":     _audio_loop,
    "blank":     _blank_loop,
}

_current_stop   = None
_current_thread = None

def _start_mode(idx):
    global _current_stop, _current_thread, _led_stop, _led_thread

    # Arrêter le thread actuel plus proprement
    if _current_stop:
        _current_stop.set()
    if _current_thread:
        _current_thread.join(timeout=3)
        if _current_thread.is_alive():
            print(f"[MODE] Thread actif encore en cours, forçage arrêt")

    # Arrêter l'ancien thread LED
    if _led_stop:
        _led_stop.set()
    if _led_thread:
        _led_thread.join(timeout=3)
        if _led_thread.is_alive():
            print(f"[LED] Thread LED encore en cours, forçage arrêt")

    name = MODES[idx % len(MODES)]
    stop = threading.Event()
    t    = threading.Thread(target=MODE_FNS[name], args=(stop,), daemon=True)
    _current_stop   = stop
    _current_thread = t
    t.start()

    # Démarrer le thread LED correspondant
    if _LED:
        # Obtenir les couleurs LED pour ce mode
        if name in _LED_COLORS and _LED_COLORS[name]:
            fixed = _LED_COLORS[name]
            get_c = lambda: fixed
        else:
            get_c = _palette_colors

        ls = threading.Event()
        lt = threading.Thread(target=_led.run, args=(ls, get_c), daemon=True)
        _led_stop   = ls
        _led_thread = lt
        lt.start()
        print(f"[LED] Thread démarré pour {name}")

    print(f"  >  mode -> {name}")

def _switch(delta):
    global _mode_idx
    with _mode_lock:
        _mode_idx = (_mode_idx + delta) % len(MODES)
        _start_mode(_mode_idx)
        _save_state()

def _switch_hud(delta):
    global _hud_idx
    with _mode_lock:  # Protéger l'accès à _hud_idx
        _hud_idx = (_hud_idx + delta) % len(HUD_STYLES)
        hud = HUD_STYLES[_hud_idx]

        # Amorce les compteurs si on arrive sur tiles/matrix
        if hud == "tiles":
            render_tiles._rates()
        elif hud == "matrix":
            render_matrix._rates()

        _save_state()
        print(f"  >  hud  -> {hud}")


def _resync_lyrics():
    """Force resync des lyrics via Ctrl+Shift+L"""
    try:
        import lyrics_source
        lyrics_source.resync()
    except Exception as e:
        print(f"[LYRICS] Erreur resync: {e}")

def _resync_youtube():
    """Force resync via YouTube timecode via Ctrl+Shift+R"""
    try:
        import lyrics_source
        # Réinitialise les données YouTube pour forcer une nouvelle détection
        lyrics_source.resync()
        print("[LYRICS] 🔄 YouTube timecode resync demandé")
    except Exception as e:
        print(f"[LYRICS] Erreur YouTube resync: {e}")


# ── Auto mode functions ────────────────────────────────────────────────────
def _apply_auto_mode(mode_name, hud_name, led_cfg):
    """Applique le mode, HUD et config LED déterminés par l'auto-mode."""
    global _mode_idx, _hud_idx

    print(f"[AUTO] apply_auto_mode: mode={mode_name}, hud={hud_name}")

    # Mapping des noms de modes depuis auto_mode.py vers MODES locaux
    MODE_MAPPING = {
        # Modes directs
        "ascii_vhs": "ascii_vhs",
        "video": "video",
        "image": "image",
        "blank": "blank",
        # Modes complexes → mapping intelligent
        "video+lyrics": "video",      # vidéo avec HUD lyrics
        "image+lyrics": "image",      # image avec HUD lyrics
        "lyrics_cascade": "audio",    # audio avec HUD intelligent
        "lyrics": "audio",            # audio avec HUD lyrics
        "audio_viz": "audio",         # mode audio visualizer
        "ascii_hud": "ascii_vhs",     # ASCII mode
        "audio": "audio",             # mode audio
        "idle": "blank",              # idle → blank
        "manual": "ascii_vhs",        # manual → ASCII
    }

    # Appliquer le mode
    mapped_mode = MODE_MAPPING.get(mode_name, mode_name)
    if mapped_mode in MODES:
        target_idx = MODES.index(mapped_mode)
        if target_idx != _mode_idx:
            with _mode_lock:
                _mode_idx = target_idx
                _start_mode(_mode_idx)
            print(f"[AUTO] Mode → {mapped_mode} (from {mode_name})")
        else:
            print(f"[AUTO] Mode {mapped_mode} déjà actif")
    else:
        print(f"[AUTO] Mode {mode_name} (mapped to {mapped_mode}) PAS dans MODES: {MODES}")

    # Appliquer le HUD
    if hud_name and hud_name in HUD_STYLES:
        target_idx = HUD_STYLES.index(hud_name)
        if target_idx != _hud_idx:
            _hud_idx = target_idx
            if hud_name == "tiles":
                render_tiles._rates()
            elif hud_name == "matrix":
                render_matrix._rates()
            print(f"[AUTO] HUD → {hud_name} (idx={target_idx})")
        else:
            print(f"[AUTO] HUD {hud_name} déjà actif")
    else:
        print(f"[AUTO] HUD {hud_name} pas dans HUD_STYLES, utilisation du défaut")

    # Appliquer la config LED
    if _LED and led_cfg:
        try:
            import led_fans as _lf_mod
            from control_server import _apply_led

            # Accès direct aux attributs du dataclass LEDConfig
            case_mode = led_cfg.case_mode
            fan_mode = led_cfg.fan_mode
            case_color = led_cfg.case_color
            fan_color = led_cfg.fan_color

            if case_mode:
                _apply_led(_lf_mod, "case_mode", case_mode)
            if fan_mode:
                _apply_led(_lf_mod, "fan_mode", fan_mode)

            # Appliquer les couleurs si présentes
            if case_color:
                _apply_led(_lf_mod, "case_color", case_color)
            if fan_color:
                _apply_led(_lf_mod, "fan_color", fan_color)

            print(f"[AUTO] LED → case={case_mode}, fans={fan_mode}")
        except Exception as e:
            print(f"[AUTO] Erreur LED: {e}")


def _auto_tick():
    """Tick d'auto-mode : détecte contexte et applique les changements."""
    global _last_context, _last_auto_check, _last_applied_mode, _last_applied_led

    try:
        import auto_mode
        context = auto_mode.detect_context()
        now = time.monotonic()

        # Initialisation ou changement drastique (musique s'arrête)
        if _last_context is None or auto_mode.should_reevaluate(_last_context, context):
            (mode, hud), led_cfg = auto_mode.determine_mode(context)
            # N'appliquer que si quelque chose a changé
            current_state = (mode, hud, led_cfg.case_mode, led_cfg.fan_mode)
            if current_state != _last_applied_mode:
                _apply_auto_mode(mode, hud, led_cfg)
                _last_applied_mode = current_state
            _last_context = context
            _last_auto_check = now
            return

        # Vérification périodique si musique active (pour lyrics)
        if context.get("has_music") and (now - _last_auto_check > _auto_check_interval):
            if context.get("has_lyrics") != _last_context.get("has_lyrics"):
                (mode, hud), led_cfg = auto_mode.determine_mode(context)
                # N'appliquer que si quelque chose a changé
                current_state = (mode, hud, led_cfg.case_mode, led_cfg.fan_mode)
                if current_state != _last_applied_mode:
                    _apply_auto_mode(mode, hud, led_cfg)
                    _last_applied_mode = current_state
                    print(f"[AUTO] Lyrics changed: {context.get('has_lyrics')}")
            _last_context = context
            _last_auto_check = now

    except Exception as e:
        print(f"[AUTO] Error: {e}")


# ══════════════════════════════════════════════════════════════════════════
# Hotkeys globaux
# ══════════════════════════════════════════════════════════════════════════

def _setup_hotkeys():
    if not _KEYBOARD_LIB:
        print("[Hotkeys] lib 'keyboard' introuvable — pip install keyboard")
        return
    _keyboard.add_hotkey("ctrl+shift+right", lambda: threading.Thread(target=_switch,     args=(+1,), daemon=True).start(), suppress=False)
    _keyboard.add_hotkey("ctrl+shift+left",  lambda: threading.Thread(target=_switch,     args=(-1,), daemon=True).start(), suppress=False)
    _keyboard.add_hotkey("ctrl+shift+up",    lambda: threading.Thread(target=_switch_hud, args=(+1,), daemon=True).start(), suppress=False)
    _keyboard.add_hotkey("ctrl+shift+down",  lambda: threading.Thread(target=_switch_hud, args=(-1,), daemon=True).start(), suppress=False)
    _keyboard.add_hotkey("ctrl+shift+l",     _resync_lyrics, suppress=False)
    _keyboard.add_hotkey("ctrl+shift+r",     _resync_youtube, suppress=False)
    print("[Hotkeys] OK (ctrl+shift+left/right  modes  |  ctrl+shift+up/down  HUD style  |  ctrl+shift+l  resync lyrics  |  ctrl+shift+r  YouTube timecode resync)")


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Check for --auto flag
    if "--auto" in _sys.argv:
        _auto_enabled = True
        print("[AUTO] Mode auto activé")
        # Bypass state loading for auto mode
        _mode_idx, _hud_idx = 0, 0  # Start at first mode, will be overridden by auto

    print("Démarrage du panel...")
    _panel = Panel()
    render.psutil.cpu_percent(interval=None)
    render_tiles._rates()
    render_matrix._rates()

    # Hot reload render.py
    def _watch_render():
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            class R(FileSystemEventHandler):
                def on_modified(self, e):
                    if Path(e.src_path).name == "render.py":
                        try: importlib.reload(render); print("render.py rechargé")
                        except Exception as ex: print(f"Erreur: {ex}")
            obs = Observer()
            obs.schedule(R(), str(Path(__file__).parent), recursive=False)
            obs.start()
        except ImportError:
            pass
    threading.Thread(target=_watch_render, daemon=True).start()

    _start_mode(_mode_idx)

    _setup_hotkeys()

    # ── Dashboard (HTTP + app native) ──────────────────────────────────────
    import bpm_source as _bpm
    _bpm.start()

    try:
        import lyrics_source as _lyrics
        _lyrics.start()
    except ImportError:
        print("[Lyrics] lyrics_source introuvable — mode lyrics désactivé")
        _lyrics = None

    # Audio visualization uses bpm_source data (already started above)

    def _get_state():
        ts, seq = _bpm.get_beat_event()
        state = {
            "mode":     MODES[_mode_idx],
            "hud":      HUD_STYLES[_hud_idx],
            "bpm":      _bpm.get_bpm(),
            "level":    _bpm.get_level(),
            "beat_seq": seq,
        }
        # Ajouter force_viz si disponible
        try:
            import render
            state["force_viz"] = render.get_force_viz()
        except:
            state["force_viz"] = False
        return state
    _set_led_fn = None
    if _LED:
        try:
            import led_fans as _lf_mod
            from control_server import _apply_led
            _set_led_fn = lambda zone, val: _apply_led(_lf_mod, zone, val)
        except Exception:
            pass
    # Callback d'override pour auto_mode
    def _auto_override_cb(mode=None, hud=None):
        try:
            import auto_mode
            auto_mode.set_manual_override(mode=mode, hud=hud)
        except Exception as e:
            print(f"[AUTO] Erreur callback override: {e}")

    control_server.start(_switch, _switch_hud, _get_state, _set_led_fn, _auto_override_cb)

    W = 44
    print()
    print("+" + "-"*W + "+")
    print(f"|  BOX SCREEN" + " "*(W-12) + "|")
    print("+" + "-"*W + "+")
    auto_label = "AUTO" if _auto_enabled else "MANUAL"
    print(f"|  mode  : {auto_label + ' / ' + MODES[_mode_idx]:<{W-10}}|")
    print(f"|  hud   : {HUD_STYLES[_hud_idx]:<{W-10}}|")
    print(f"|  dashboard : http://localhost:7420{' '*(W-35)}|")
    print("+" + "-"*W + "+")
    print(f"|  ctrl+shift+left/right  -> mode{' '*(W-28)}|")
    print(f"|  ctrl+shift+up/down    -> hud style{' '*(W-32)}|")
    print(f"|  ctrl+c                -> quit{' '*(W-26)}|")
    print("+" + "-"*W + "+")
    print()

    # App native Dear PyGui (--gui pour l'activer)
    try:
        if "--gui" in _sys.argv:
            import dashboard_app
            dashboard_app.run_embedded(_switch, _switch_hud, _get_state, _set_led_fn if _LED else None)
    except ImportError:
        pass

    try:
        while True:
            # Auto-mode tick
            if _auto_enabled:
                _auto_tick()

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nArrêt")
    finally:
        if _current_stop: _current_stop.set()
        if _led_stop:     _led_stop.set()
        if _LED:          _led.shutdown()
        _panel.close()
        # Flush lyrics cache et arrêt propre des sources
        try:
            _bpm.stop()
        except:
            pass
        try:
            _lyrics.stop()
        except:
            pass
