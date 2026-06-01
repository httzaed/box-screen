"""
Panel launcher — switche entre les modes avec Ctrl+Shift+Fleche
  Ctrl+Shift+Droite : mode suivant
  Ctrl+Shift+Gauche : mode précédent

Modes disponibles : ascii_vhs, video, image
"""
import time, math, random, importlib, threading, io, struct
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from pynput import keyboard

import sys as _sys
import json as _json

import render
from panel import Panel, WIDTH, HEIGHT, _image_to_jpeg, _send_frame

# ── LED wave (projet ~/LEDs) ────────────────────────────────────────────────
_sys.path.insert(0, str(Path(__file__).parent.parent / "LEDs"))
try:
    import led_wave as _led
    _LED = True
except ImportError:
    _led = None
    _LED = False
    print("[LED] led_wave introuvable — LEDs désactivées")

# ── Config ─────────────────────────────────────────────────────────────────
_HERE       = Path(__file__).parent
VIDEO_FILE  = str(next(_HERE.glob("*.mp4"),  _HERE / "video.mp4"))
IMAGE_FILE  = str(next(_HERE.glob("*.png"),  _HERE / "image.png"))
ASCII_FILE  = _HERE / "ascii_bg.txt"

MODES       = ["ascii_vhs", "video", "image"]
FPS_VIDEO   = 25
FPS_ASCII   = 14
FPS_IMAGE   = 1

# ── ASCII VHS config ───────────────────────────────────────────────────────
ASCII_START = (221,   0, 255)   # violet/rose vif — identique à st_wave.py
ASCII_END   = (255,  40,   0)   # orange-rouge pétant — identique à st_wave.py
ASCII_BG    = (6, 4, 12)
ASCII_SIZE  = 13
INTENSITY   = 0.4

# ── LED couleurs par mode ───────────────────────────────────────────────────
_LED_COLORS = {
    "ascii_vhs": (ASCII_START, ASCII_END),           # violet → orange comme l'écran
    "image":     ((0, 162, 216), (0, 216, 54)),      # cyan ciel → vert jungle
    "video":     ((255, 20, 100), (50, 100, 220)),      # rose vif → bleu acier
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

# ── State ──────────────────────────────────────────────────────────────────
_mode_idx   = 0
_mode_lock  = threading.Lock()
_stop_event = threading.Event()
_panel      = None
_led_stop   = None
_led_thread = None


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
    render.background_override = img
    frame = render.build_frame()
    _panel.send_image(frame, fit=False)

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
    from panel import _image_to_jpeg, _send_frame
    import datetime as _dt

    bg          = _fit_crop(Image.open(IMAGE_FILE).convert("RGB"))
    cached_jpeg = None
    last_sec    = -1

    while not stop.is_set():
        t0  = time.time()
        now = _dt.datetime.now()

        if now.second != last_sec:
            last_sec = now.second
            # Rebuild frame avec l'heure mise à jour
            render.background_override = bg
            frame_img   = render.build_frame()
            cached_jpeg = _image_to_jpeg(frame_img)

        if cached_jpeg:
            _send_frame(_panel._ep_out, _panel._ep_in, cached_jpeg)

        time.sleep(max(0, 1/FPS_IMAGE - (time.time()-t0)))


# ══════════════════════════════════════════════════════════════════════════
# Switcher
# ══════════════════════════════════════════════════════════════════════════

MODE_FNS = {
    "ascii_vhs": _ascii_vhs_loop,
    "video":     _video_loop,
    "image":     _image_loop,
}

MODE_LAYOUTS = {
    "ascii_vhs": "terminal",
    "video":     "full",
    "image":     "clock",
}

_current_stop   = None
_current_thread = None

def _start_mode(idx):
    global _current_stop, _current_thread, _led_stop, _led_thread
    if _current_stop:
        _current_stop.set()
    if _current_thread:
        _current_thread.join(timeout=2)

    # Arrêter l'ancien thread LED
    if _led_stop:
        _led_stop.set()
    if _led_thread:
        _led_thread.join(timeout=2)

    name = MODES[idx % len(MODES)]
    render.LAYOUT = MODE_LAYOUTS[name]
    stop = threading.Event()
    t    = threading.Thread(target=MODE_FNS[name], args=(stop,), daemon=True)
    _current_stop   = stop
    _current_thread = t
    t.start()

    # Démarrer le thread LED correspondant
    if _LED:
        fixed = _LED_COLORS[name]
        get_c = (lambda: fixed) if fixed else _palette_colors
        ls = threading.Event()
        lt = threading.Thread(target=_led.run, args=(ls, get_c), daemon=True)
        _led_stop   = ls
        _led_thread = lt
        lt.start()

    print(f"Mode : {name}")

def _switch(delta):
    global _mode_idx
    with _mode_lock:
        _mode_idx = (_mode_idx + delta) % len(MODES)
        _start_mode(_mode_idx)


# ══════════════════════════════════════════════════════════════════════════
# Hotkeys globaux
# ══════════════════════════════════════════════════════════════════════════

_pressed = set()

def _on_press(key):
    _pressed.add(key)
    ctrl  = keyboard.Key.ctrl_l  in _pressed or keyboard.Key.ctrl_r  in _pressed
    shift = keyboard.Key.shift_l in _pressed or keyboard.Key.shift_r in _pressed
    if ctrl and shift:
        if key == keyboard.Key.right: _switch(+1)
        if key == keyboard.Key.left:  _switch(-1)

def _on_release(key):
    _pressed.discard(key)


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Démarrage du panel...")
    _panel = Panel()
    render.psutil.cpu_percent(interval=None)

    # Hot reload render.py
    def _watch_render():
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
    threading.Thread(target=_watch_render, daemon=True).start()

    _start_mode(_mode_idx)

    listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    listener.start()

    print(f"Mode actuel : {MODES[_mode_idx]}")
    print("Ctrl+Shift+→  mode suivant")
    print("Ctrl+Shift+←  mode précédent")
    print("Ctrl+C pour quitter\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nArrêt")
    finally:
        if _current_stop: _current_stop.set()
        if _led_stop:     _led_stop.set()
        if _LED:          _led.shutdown()
        listener.stop()
        _panel.close()
