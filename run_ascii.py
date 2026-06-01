"""
ASCII art en fond + cadrans par dessus.
Usage : python run_ascii.py
"""
import time
import importlib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import render
from panel import Panel, WIDTH, HEIGHT

# ── Config ─────────────────────────────────────────────────────────────────
ASCII_FILE  = Path(__file__).parent / "ascii_bg.txt"
GRAD_LEFT   = (140, 82, 255)    # #8C52FF
GRAD_RIGHT  = (255, 160, 0)    # #FFA000 orange vif
BG_COLOR    = (8, 8, 14)      # fond


def _gradient_color(col: int, max_col: int) -> tuple:
    """Interpolation HSL — hue shortpath, saturation et luminosité maintenues élevées."""
    import colorsys
    t = col / max(1, max_col - 1)
    hl, sl, ll = colorsys.rgb_to_hls(*(c/255 for c in GRAD_LEFT))
    hr, sr, lr = colorsys.rgb_to_hls(*(c/255 for c in GRAD_RIGHT))
    # chemin court sur la roue des teintes
    dh = hr - hl
    if dh > 0.5:  dh -= 1.0
    if dh < -0.5: dh += 1.0
    h = (hl + t * dh) % 1.0
    s = sl + t * (sr - sl)
    l = ll + t * (lr - ll)
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return (int(r*255), int(g*255), int(b*255))
# ── Charger la police monospace ────────────────────────────────────────────
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

# ── Pré-rendre le fond ASCII ───────────────────────────────────────────────
def _render_ascii_bg() -> Image.Image:
    lines   = ASCII_FILE.read_text(encoding="utf-8").split("\n")
    max_col = max(len(l) for l in lines)

    # Rendu à taille fixe (8px), puis resize pour rentrer dans WIDTH×HEIGHT
    RENDER_SIZE = 8
    font = _mono(RENDER_SIZE)
    dummy = Image.new("RGB", (1, 1))
    d     = ImageDraw.Draw(dummy)
    bbox  = d.textbbox((0, 0), "@", font=font)
    cw    = max(1, bbox[2] - bbox[0])
    ch    = max(1, bbox[3] - bbox[1] + 1)

    canvas_w = cw * max_col
    canvas_h = ch * len(lines)
    print(f"ASCII canvas : {canvas_w}×{canvas_h} → resize → {WIDTH}×{HEIGHT}")

    canvas = Image.new("RGB", (canvas_w, canvas_h), BG_COLOR)
    draw   = ImageDraw.Draw(canvas)

    for row, line in enumerate(lines):
        y = row * ch
        for col, char in enumerate(line):
            if char not in (" ", "\r"):
                draw.text((col * cw, y), char, font=font, fill=_gradient_color(col, max_col))

    # Resize pour tenir dans le panel
    return canvas.resize((WIDTH, HEIGHT), Image.LANCZOS)

print("Rendu ASCII en cours...")
_ASCII_BG = _render_ascii_bg()
print("ASCII rendu OK")

# ── Hot reload render.py ───────────────────────────────────────────────────
class RenderReloader(FileSystemEventHandler):
    def __init__(self): self.dirty = False
    def on_modified(self, event):
        if Path(event.src_path).resolve() == (Path(__file__).parent / "render.py").resolve():
            self.dirty = True

# ── Main ───────────────────────────────────────────────────────────────────
panel    = Panel()
reloader = RenderReloader()
observer = Observer()
observer.schedule(reloader, str(Path(__file__).parent), recursive=False)
observer.start()

print("ASCII dashboard démarré — Ctrl+C pour quitter\n")

try:
    render.psutil.cpu_percent(interval=None)

    while True:
        if reloader.dirty:
            reloader.dirty = False
            try:
                importlib.reload(render)
                print("render.py rechargé")
            except Exception as e:
                print(f"Erreur render.py : {e}")

        t0 = time.time()
        render.background_override = _ASCII_BG
        frame = render.build_frame()
        panel.send_image(frame, fit=False)

        elapsed = time.time() - t0
        time.sleep(max(0, render.REFRESH_RATE - elapsed))

except KeyboardInterrupt:
    print("\nArrêt")
finally:
    observer.stop()
    observer.join()
    panel.close()
