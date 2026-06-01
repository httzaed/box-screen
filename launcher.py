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

import render
from panel import Panel, WIDTH, HEIGHT, _image_to_jpeg, _send_frame

# ── Config ─────────────────────────────────────────────────────────────────
VIDEO_FILE  = r"C:\Users\hedik\Downloads\This One Still Works.mp4"
IMAGE_FILE  = r"C:\Users\hedik\Downloads\jungle-landscape-pixel-art-style.png"
ASCII_FILE  = Path(__file__).parent / "ascii_bg.txt"

MODES       = ["ascii_vhs", "video", "image"]
FPS_VIDEO   = 25
FPS_ASCII   = 14
FPS_IMAGE   = 1

# ── ASCII VHS config ───────────────────────────────────────────────────────
ASCII_START = (200,  80, 255)
ASCII_END   = (255, 160,  50)
ASCII_BG    = (6, 4, 12)
ASCII_SIZE  = 13
INTENSITY   = 0.4

# ── State ──────────────────────────────────────────────────────────────────
_mode_idx   = 0
_mode_lock  = threading.Lock()
_stop_event = threading.Event()
_panel      = None


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
    for path in [
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\cour.ttf",
        r"C:\Windows\Fonts\lucon.ttf",
    ]:
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
                color = grad[col] * dim          # (3,)
                stamp = stamps[char]             # (ch, cw, 3) mask 0..1
                canvas[y:y+ch, x:x+cw] += stamp * color  # broadcast

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
    "ascii_vhs": "split",
    "video":     "full",
    "image":     "clock",
}

_current_stop   = None
_current_thread = None

def _start_mode(idx):
    global _current_stop, _current_thread
    if _current_stop:
        _current_stop.set()
    if _current_thread:
        _current_thread.join(timeout=2)
    name = MODES[idx % len(MODES)]
    render.LAYOUT = MODE_LAYOUTS[name]
    stop = threading.Event()
    t    = threading.Thread(target=MODE_FNS[name], args=(stop,), daemon=True)
    _current_stop   = stop
    _current_thread = t
    t.start()
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
        listener.stop()
        _panel.close()
