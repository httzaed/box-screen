"""
render_matrix.py -- Box Screen mode "06 . nvtop / matrix"
Trois lignes de graphes pleine largeur, vert-sur-noir facon nvtop.

Dans launcher.py :
  import render_matrix
  panel.send_image(render_matrix.build_frame())       # fond par defaut
  panel.send_image(render_matrix.build_frame(bg=img)) # sur fond existant
"""

import sys
import time
from collections import deque
from datetime import datetime

import psutil
from PIL import Image, ImageDraw, ImageFont

from render_tiles import _gpu_stats, _rates, _ema, _sample, _GPU_NAME, _mono

# ── Dimensions / cadence ──────────────────────────────────────────────────
WIDTH        = 1920
HEIGHT       = 462
REFRESH_RATE = 0.25
LAYOUT       = "matrix"

HIST_LEN     = 220
GRAPH_STYLE  = "block"

# ── Palette ──────────────────────────────────────────────────────────────
GPU_C   = (70, 224, 138)
CPU_C   = (54, 224, 200)
NET_C   = (154, 255, 200)
TEXT    = (234, 255, 244)
DIM     = (150, 168, 160)
BG_TOP  = (6, 20, 12)
BG_BOT  = (1, 4, 2)

# ── Fonts ─────────────────────────────────────────────────────────────────
F_HEAD = _mono(19)
F_LBL  = _mono(16)
F_NUM  = _mono(52, bold=True)
F_SUB  = _mono(16)

# ── Fond pre-rendu : degrade vertical + pluie discrete ───────────────────
def _make_bg():
    bg = Image.new("RGB", (WIDTH, HEIGHT), BG_BOT)
    px = bg.load()
    for y in range(HEIGHT):
        f = y / HEIGHT
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * f)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * f)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * f)
        for x in range(WIDTH):
            px[x, y] = (r, g, b)
    import random
    d = ImageDraw.Draw(bg, "RGBA")
    rain_font = _mono(15)
    random.seed(42)
    chars = "01<>/\\|[]{}=+*ABCDEF0123456789"
    for _ in range(700):
        x = random.randint(0, WIDTH)
        y = random.randint(0, HEIGHT)
        d.text((x, y), random.choice(chars), font=rain_font, fill=(70, 224, 138, 16))
    return bg

_BG = _make_bg()

# Overlay pluie seul (RGBA) pour composite sur fond externe
def _make_rain_overlay():
    rain = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(rain, "RGBA")
    import random
    random.seed(42)
    rain_font = _mono(15)
    chars = "01<>/\\|[]{}=+*ABCDEF0123456789"
    for _ in range(700):
        x = random.randint(0, WIDTH)
        y = random.randint(0, HEIGHT)
        d.text((x, y), random.choice(chars), font=rain_font, fill=(70, 224, 138, 22))
    return rain

_RAIN = _make_rain_overlay()

# ── Etat glissant ─────────────────────────────────────────────────────────
_h = {k: deque([0.0] * HIST_LEN, maxlen=HIST_LEN) for k in ("gpu", "cpu", "net")}


# ── Graphe ────────────────────────────────────────────────────────────────
def _block_graph(draw, x, y, w, h, data, color, vmax):
    cols = max(10, int(w / 6))
    bw = w / cols
    samp = _sample(data, cols)
    vmax = max(1e-6, vmax)
    for i, v in enumerate(samp):
        f = max(0.0, min(1.0, v / vmax))
        bh = max(1.0, f * h)
        bx = x + i * bw
        a = int(70 + 185 * (i / cols))
        draw.rectangle([bx, y + h - bh, bx + bw - 1.4, y + h], fill=(*color, a))


def _line_graph(draw, x, y, w, h, data, color, vmax):
    n = max(12, int(w / 8))
    samp = _sample(data, n)
    vmax = max(1e-6, vmax)
    pts = [(x + (i / (n - 1)) * w, y + h - max(0.0, min(1.0, v / vmax)) * h)
           for i, v in enumerate(samp)]
    draw.polygon(pts + [(x + w, y + h), (x, y + h)], fill=(*color, 50))
    draw.line(pts, fill=(*color, 255), width=2, joint="curve")


def _graph(draw, x, y, w, h, data, color, vmax):
    (_line_graph if GRAPH_STYLE == "line" else _block_graph)(draw, x, y, w, h, data, color, vmax)


# ── Frame ──────────────────────────────────────────────────────────────────
def build_frame(bg: "Image.Image | None" = None) -> Image.Image:
    if bg is not None:
        # Assombrir le fond pour garder la lisibilite des graphes
        img = bg.copy().convert("RGBA")
        dark = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 160))
        img.alpha_composite(dark)
        img.alpha_composite(_RAIN)
        img = img.convert("RGB")
    else:
        img = _BG.copy()
    draw = ImageDraw.Draw(img, "RGBA")

    cpu = _ema("m_cpu", psutil.cpu_percent(interval=None))
    ram = psutil.virtual_memory().percent
    freq = psutil.cpu_freq()
    ghz = (freq.current / 1000) if freq else 0.0
    gpu, vram, gputemp, vram_u, vram_t = _gpu_stats()
    gpu = _ema("m_gpu", gpu)
    try:
        ct = psutil.sensors_temperatures()
        cputemp = next((s.current for arr in ct.values() for s in arr if s.current), 40 + cpu * 0.4)
    except Exception:
        cputemp = 40 + cpu * 0.4
    dn, up, rd, wr = _rates()

    _h["gpu"].append(gpu)
    _h["cpu"].append(cpu)
    _h["net"].append(dn)

    now = datetime.now()

    PAD = 24
    draw.text((PAD, 12), "nvtop . trofeo", font=F_HEAD, fill=(*GPU_C, 255))
    hw = draw.textbbox((0, 0), "nvtop . trofeo ", font=F_HEAD)[2]
    draw.text((PAD + hw, 12), f"-- {_GPU_NAME}", font=F_HEAD, fill=(*DIM, 255))
    ts = now.strftime("%H:%M:%S")
    tw = draw.textbbox((0, 0), ts, font=F_HEAD)[2]
    draw.text((WIDTH - PAD - tw, 12), ts, font=F_HEAD, fill=(*TEXT, 255))

    top = 48
    bottom = HEIGHT - 14
    lane_h = (bottom - top) / 3
    lanes = [
        ("GPU LOAD", f"{round(gpu)}%",
         f"{round(gputemp)}C  vram {vram_u:.1f}/{vram_t:.1f}G", _h["gpu"], GPU_C, 100),
        ("CPU LOAD", f"{round(cpu)}%",
         f"{ghz:.2f}GHz  {round(cputemp)}C  mem {round(ram)}%", _h["cpu"], CPU_C, 100),
        ("NET RX", f"{dn:.0f}",
         f"MB/s  tx {up:.1f}", _h["net"], NET_C, max(10.0, max(_h["net"]))),
    ]

    label_w = 200
    for i, (label, val, sub, data, color, vmax) in enumerate(lanes):
        ly = top + i * lane_h
        draw.line([(PAD, ly), (WIDTH - PAD, ly)], fill=(255, 255, 255, 26), width=1)
        bx = PAD
        draw.text((bx, ly + 14), label, font=F_LBL, fill=(*DIM, 255))
        draw.text((bx, ly + 32), val, font=F_NUM, fill=(*TEXT, 255))
        draw.text((bx, ly + 90), sub, font=F_SUB, fill=(*color, 255))
        gx = bx + label_w
        gw = WIDTH - PAD - gx
        gh = lane_h - 30
        _graph(draw, gx, ly + (lane_h - gh) - 4, gw, gh, data, color, vmax)

    return img


# ── Standalone ────────────────────────────────────────────────────────────
def _run_on_panel():
    from panel import Panel
    psutil.cpu_percent(interval=None)
    _rates()
    panel = Panel()
    print("Mode : 06 . nvtop / matrix -- Ctrl+C pour quitter")
    try:
        while True:
            t0 = time.time()
            panel.send_image(build_frame(), fit=False)
            time.sleep(max(0, REFRESH_RATE - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nArret")
    finally:
        panel.close()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        psutil.cpu_percent(interval=None)
        _rates()
        time.sleep(0.3)
        for _ in range(20):
            build_frame()
            time.sleep(0.05)
        build_frame().save(sys.argv[2])
        print(f"Apercu ecrit --> {sys.argv[2]}")
    else:
        _run_on_panel()
