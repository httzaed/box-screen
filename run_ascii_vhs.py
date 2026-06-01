"""
ASCII VHS animé en fond + cadrans par dessus.
Port du terminal VHS animator → PIL image pour le panel.
"""
import math, random, time, importlib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import render
from panel import Panel, WIDTH, HEIGHT

# ── Config ─────────────────────────────────────────────────────────────────
ASCII_FILE  = Path(__file__).parent / "ascii_bg.txt"
FPS         = 14
INTENSITY   = 0.4
START_RGB   = (200,  80, 255)   # violet vif + lumineux
END_RGB     = (255, 160,  50)   # orange vif + lumineux
BG_COLOR    = (6, 4, 12)
CHAR_SIZE   = 13

# ── Font monospace ─────────────────────────────────────────────────────────
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

FONT = _mono(CHAR_SIZE)

# Mesure d'un caractère
_dummy = Image.new("RGB", (1, 1))
_dd    = ImageDraw.Draw(_dummy)
_bbox  = _dd.textbbox((0, 0), "@", font=FONT)
CW     = max(1, _bbox[2] - _bbox[0])
CH     = max(1, _bbox[3] - _bbox[1] + 2)

# ── Charger ASCII ──────────────────────────────────────────────────────────
_raw_lines = ASCII_FILE.read_text(encoding="utf-8").split("\n")
COLS       = max(len(l) for l in _raw_lines)
ROWS       = len(_raw_lines)

def _pad(lines, cols):
    return [ln[:cols].ljust(cols) for ln in lines]

# ── VHS effects (portés du script terminal) ────────────────────────────────
def _lerp(a, b, t): return a + (b - a) * t

def _hshift(line, dx, cols):
    if dx > 0:
        out = " " * dx + line
    elif dx < 0:
        out = line[abs(dx):]
    else:
        out = line
    return out[:cols].ljust(cols)

def _char_noise(line, prob=0.008):
    if prob <= 0: return line
    arr = list(line)
    for i, c in enumerate(arr):
        if c != " " and random.random() < prob:
            arr[i] = random.choice(["`", "'", ".", ",", "-"])
    return "".join(arr)

def _band_tear(lines, cols, band_h=2, max_dx=6):
    if not lines: return lines
    start = random.randint(0, max(0, len(lines) - band_h))
    out = []
    for i, ln in enumerate(lines):
        if start <= i < start + band_h:
            out.append(_hshift(ln, random.choice([-max_dx, max_dx]), cols))
        else:
            out.append(ln)
    return out

def vhs_frame(lines, cols, frame, intensity):
    t   = frame / 30.0
    out = []
    vshift = 0
    if random.random() < 0.03 * intensity:
        vshift = random.choice([-1, 1])

    amp  = 2 + 6 * intensity
    freq = 0.18 + 0.25 * intensity

    for idx, ln in enumerate(lines):
        phase   = t * 2 * math.pi * freq + idx * 0.12
        dx      = int(amp * math.sin(phase))
        wobbled = _hshift(ln, dx, cols)
        noisy   = _char_noise(wobbled, prob=0.004 + 0.02 * intensity)
        out.append(noisy)

    if vshift != 0:
        if vshift > 0:
            out = [" " * cols] * vshift + out[:-vshift]
        else:
            vv  = abs(vshift)
            out = out[vv:] + [" " * cols] * vv

    if random.random() < 0.05 * intensity:
        out = _band_tear(out, cols, band_h=random.randint(1, 2),
                         max_dx=int(4 + 6 * intensity))
    return out

# ── Rendu PIL ──────────────────────────────────────────────────────────────
def render_ascii_frame(lines, frame) -> Image.Image:
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    total_w = CW * COLS
    total_h = CH * len(lines)
    x_off   = (WIDTH  - total_w) // 2
    y_off   = (HEIGHT - total_h) // 2

    for row, line in enumerate(lines):
        y = y_off + row * CH
        if y + CH < 0 or y > HEIGHT:
            continue
        # Dim effect : lignes paires légèrement moins lumineuses
        dim = 0.6 if row % 2 == 1 else 1.0
        for col, char in enumerate(line):
            if char in (" ", "\r"):
                continue
            x = x_off + col * CW
            if x < 0 or x >= WIDTH:
                continue
            # Gradient horizontal
            t = col / max(1, COLS - 1)
            r = int(_lerp(START_RGB[0], END_RGB[0], t) * dim)
            g = int(_lerp(START_RGB[1], END_RGB[1], t) * dim)
            b = int(_lerp(START_RGB[2], END_RGB[2], t) * dim)
            draw.text((x, y), char, font=FONT, fill=(r, g, b))

    return img

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

print(f"ASCII VHS dashboard — {COLS}×{ROWS} chars  {CW}×{CH}px/char")
print("Modifie render.py à chaud — Ctrl+C pour quitter\n")

base_lines = _pad(_raw_lines, COLS)

try:
    render.psutil.cpu_percent(interval=None)
    frame = 0

    while True:
        if reloader.dirty:
            reloader.dirty = False
            try:
                importlib.reload(render)
                print("render.py rechargé")
            except Exception as e:
                print(f"Erreur render.py : {e}")

        t0 = time.time()

        # VHS + rendu fond
        vhs_lines = vhs_frame(base_lines, COLS, frame, INTENSITY)
        bg        = render_ascii_frame(vhs_lines, frame)

        # Cadrans par dessus
        render.background_override = bg
        out = render.build_frame()
        panel.send_image(out, fit=False)

        elapsed = time.time() - t0
        time.sleep(max(0, 1 / FPS - elapsed))
        frame += 1

except KeyboardInterrupt:
    print("\nArrêt")
finally:
    observer.stop()
    observer.join()
    panel.close()
