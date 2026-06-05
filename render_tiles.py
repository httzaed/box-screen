"""
render_tiles.py — Box Screen mode "04 · TUI tiles"
═══════════════════════════════════════════════════════════════════════════
Grille de tuiles façon dashboard terminal (grafana-dark) pour le Trofeo
Vision 9.16 (1920x462). Reproduit le mock HTML "TUI tiles".

Deux façons de l'utiliser :

  1. Standalone — branche le panneau et lance :
         python render_tiles.py
     (ou `python render_tiles.py --preview tiles.png` pour écrire une image
      de test sans matériel)

  2. Dans launcher.py — importe build_frame() et envoie le résultat :
         import render_tiles
         panel.send_image(render_tiles.build_frame())
     (build_frame() rend une image RGB 1920x462, à l'endroit ; panel.py
      gère le resize + rotation 180° + JPEG.)

Stats réelles via psutil + NVML/nvidia-smi. Historique glissant par métrique
pour les mini-graphes.
"""

import sys
import time
import shutil
import pathlib
from collections import deque
from datetime import datetime

import psutil
from PIL import Image, ImageDraw, ImageFont

# ── Dimensions / cadence ───────────────────────────────────────────────────
WIDTH       = 1920
HEIGHT      = 462
REFRESH_RATE = 0.25          # 4 Hz — mouvement fluide des graphes
LAYOUT      = "tiles"        # pour cohérence avec render.py

HIST_LEN    = 80             # échantillons gardés par métrique
GRAPH_STYLE = "block"        # "block" (▇ colonnes) ou "line" (aire lissée)

# ── Palette (matrix green par défaut, surchargée par palette.json) ─────────
ACCENT   = (70, 224, 138)
ACCENT2  = (122, 240, 192)
TEXT     = (234, 255, 244)
DIM      = (150, 168, 160)
WARM     = (255, 194, 74)
CRIT     = (255, 84, 112)
BG       = (8, 10, 14)
TILE_BG  = (255, 255, 255, 7)
BORDER   = (255, 255, 255, 36)

try:
    import json
    _pal = json.loads((pathlib.Path(__file__).parent / "palette.json").read_text())
    if _pal.get("accent"):
        ACCENT = tuple(_pal["accent"][:3])
except Exception:
    pass

# ── Couleur LED → accent dynamique ────────────────────────────────────────
_LED_RGB = {
    "violet": (180,  40, 255),
    "cyan":   ( 30, 200, 255),
    "blue":   (  0,  80, 255),
    "teal":   (  0, 210, 150),
    "green":  (  0, 212, 130),
    "yellow": (255, 200,   0),
    "orange": (255,  85,   0),
    "red":    (255,   0,   0),
    "pink":   (255,  20, 100),
    "white":  (220, 220, 220),
}

def _led_accent_color() -> tuple:
    """Lit case_color depuis led_config.json → RGB. Fallback vert."""
    try:
        cfg = json.loads((pathlib.Path(__file__).parent / "led_config.json").read_text())
        return _LED_RGB.get(cfg.get("case_color", "violet"), (70, 224, 138))
    except Exception:
        return (70, 224, 138)

# ── Fonts ──────────────────────────────────────────────────────────────────
def _mono(size, bold=False):
    names = (
        ["C:/Windows/Fonts/consolab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
         "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/liberation/LiberationMono-Bold.ttf"]
        if bold else
        ["C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
         "/usr/share/fonts/TTF/DejaVuSansMono.ttf", "/usr/share/fonts/liberation/LiberationMono-Regular.ttf"]
    )
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    return ImageFont.load_default()

F_TITLE  = _mono(19)
F_HEAD   = _mono(19)
F_NUM    = _mono(48, bold=True)
F_NUMSM  = _mono(40, bold=True)
F_UNIT   = _mono(19)
F_SUB    = _mono(18)

# ── GPU ─────────────────────────────────────────────────────────────────────
try:
    import pynvml
    pynvml.nvmlInit()
    _gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
    try:
        _GPU_NAME = pynvml.nvmlDeviceGetName(_gpu)
        if isinstance(_GPU_NAME, bytes):
            _GPU_NAME = _GPU_NAME.decode()
    except Exception:
        _GPU_NAME = "GPU"
    _GPU_OK = "pynvml"
except Exception:
    _GPU_OK = "smi" if shutil.which("nvidia-smi") else False
    _GPU_NAME = "GPU"


def _gpu_stats():
    """→ (usage %, vram %, temp °C, vram_used GB, vram_total GB)"""
    if _GPU_OK == "pynvml":
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(_gpu)
            mem  = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
            temp = pynvml.nvmlDeviceGetTemperature(_gpu, pynvml.NVML_TEMPERATURE_GPU)
            return (float(util.gpu), mem.used / mem.total * 100, float(temp),
                    mem.used / 1e9, mem.total / 1e9)
        except Exception:
            pass
    if _GPU_OK == "smi":
        try:
            import subprocess
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"], text=True).strip()
            g, temp, mu, mt = map(float, out.split(","))
            return g, mu / mt * 100, temp, mu / 1024, mt / 1024
        except Exception:
            pass
    return 0.0, 0.0, 0.0, 0.0, 1.0


# ── État glissant ────────────────────────────────────────────────────────────
_hist = {k: deque([0.0] * HIST_LEN, maxlen=HIST_LEN)
         for k in ("cpu", "gpu", "ram", "vram", "cputemp", "gputemp", "disk", "freq")}
_smooth = {}
_prev_net = None
_prev_disk = None


def _ema(key, value, alpha=0.25):
    _smooth[key] = value if key not in _smooth else _smooth[key] * (1 - alpha) + value * alpha
    return _smooth[key]


def _rates():
    """Débits réseau & disque (MB/s) calculés sur l'intervalle réel."""
    global _prev_net, _prev_disk
    now = time.time()
    net = psutil.net_io_counters()
    dn = up = 0.0
    if _prev_net:
        dt = max(1e-3, now - _prev_net[2])
        dn = (net.bytes_recv - _prev_net[0]) / dt / 1e6
        up = (net.bytes_sent - _prev_net[1]) / dt / 1e6
    _prev_net = (net.bytes_recv, net.bytes_sent, now)

    rd = wr = 0.0
    dio = psutil.disk_io_counters()
    if dio and _prev_disk:
        dt = max(1e-3, now - _prev_disk[2])
        rd = (dio.read_bytes - _prev_disk[0]) / dt / 1e6
        wr = (dio.write_bytes - _prev_disk[1]) / dt / 1e6
    if dio:
        _prev_disk = (dio.read_bytes, dio.write_bytes, now)
    return max(0, dn), max(0, up), max(0, rd), max(0, wr)


def _status_color(pct):
    if pct >= 85:
        return CRIT
    if pct >= 65:
        return WARM
    return ACCENT


def _sample(data, n):
    d = list(data)
    if len(d) >= n:
        return d[-n:]
    return [0.0] * (n - len(d)) + d


# ── Primitives de dessin ─────────────────────────────────────────────────────
def _block_graph(draw, x, y, w, h, data, color, vmin=0.0, vmax=100.0):
    cols = max(6, int(w / 6))
    bw = w / cols
    samp = _sample(data, cols)
    span = max(1e-6, vmax - vmin)
    for i, v in enumerate(samp):
        f = max(0.0, min(1.0, (v - vmin) / span))
        bh = max(1.0, f * h)
        bx = x + i * bw
        a = int(90 + 165 * (i / cols))
        draw.rectangle([bx, y + h - bh, bx + bw - 1.4, y + h], fill=(*color, a))


def _line_graph(draw, x, y, w, h, data, color, vmin=0.0, vmax=100.0):
    n = max(8, int(w / 8))
    samp = _sample(data, n)
    span = max(1e-6, vmax - vmin)
    pts = []
    for i, v in enumerate(samp):
        f = max(0.0, min(1.0, (v - vmin) / span))
        pts.append((x + (i / (n - 1)) * w, y + h - f * h))
    # aire
    draw.polygon(pts + [(x + w, y + h), (x, y + h)], fill=(*color, 46))
    # ligne
    draw.line(pts, fill=(*color, 255), width=2, joint="curve")


def _graph(draw, x, y, w, h, data, color, vmin=0.0, vmax=100.0):
    (_line_graph if GRAPH_STYLE == "line" else _block_graph)(draw, x, y, w, h, data, color, vmin, vmax)


def _tile(draw, x, y, w, h, title, accent, dot_pct=None):
    """Cadre + titre ┤ … ├ + pastille d'état. Renvoie le rect de contenu."""
    r = 8
    draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=TILE_BG, outline=BORDER, width=1)
    # titre
    pad = 12
    tb = "┤ "
    draw.text((x + pad, y + 8), "┤", font=F_TITLE, fill=(*accent, 130))
    bw_b = draw.textbbox((0, 0), "┤ ", font=F_TITLE)[2]
    draw.text((x + pad + bw_b, y + 8), title, font=F_TITLE, fill=(*accent, 255))
    bw_t = draw.textbbox((0, 0), title, font=F_TITLE)[2]
    draw.text((x + pad + bw_b + bw_t, y + 8), " ├", font=F_TITLE, fill=(*accent, 130))
    # pastille
    if dot_pct is not None:
        dc = _status_color(dot_pct)
        ds = 8
        dx = x + w - pad - ds
        dy = y + 13
        draw.ellipse([dx, dy, dx + ds, dy + ds], fill=(*dc, 255))
    return (x + pad, y + 38, w - 2 * pad, h - 38 - 12)


def _big_number(draw, cx_left, cy_bottom, value, unit, color):
    s = str(value)
    draw.text((cx_left, cy_bottom - 48), s, font=F_NUMSM, fill=(*color, 255))
    sw = draw.textbbox((0, 0), s, font=F_NUMSM)[2]
    if unit:
        draw.text((cx_left + sw + 4, cy_bottom - 26), unit, font=F_UNIT, fill=(*DIM, 255))


# ── Frame ─────────────────────────────────────────────────────────────────────
def build_frame(bg: "Image.Image | None" = None) -> Image.Image:
    global ACCENT, ACCENT2
    _a = _led_accent_color()
    ACCENT  = _a
    ACCENT2 = tuple(min(255, int(c * 1.25)) for c in _a)

    if bg is not None:
        img = bg.copy()
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img, "RGBA")

    # — stats —
    cpu  = _ema("cpu",  psutil.cpu_percent(interval=None))
    ramv = psutil.virtual_memory()
    ram  = _ema("ram",  ramv.percent)
    freq = psutil.cpu_freq()
    ghz  = _ema("freq", (freq.current / 1000) if freq else 0.0)
    gpu, vram, gputemp, vram_u, vram_t = _gpu_stats()
    gpu     = _ema("gpu", gpu)
    vram    = _ema("vram", vram)
    gputemp = _ema("gputemp", gputemp)
    try:
        ct = psutil.sensors_temperatures()
        cputemp = next((s.current for arr in ct.values() for s in arr if s.current), 40 + cpu * 0.4)
    except Exception:
        cputemp = 40 + cpu * 0.4
    cputemp = _ema("cputemp", cputemp)
    dn, up, rd, wr = _rates()
    disk_act = min(100.0, (rd + wr) / 5.2)

    for k, v in (("cpu", cpu), ("gpu", gpu), ("ram", ram), ("vram", vram),
                 ("cputemp", cputemp), ("gputemp", gputemp), ("disk", disk_act),
                 ("freq", ghz)):
        _hist[k].append(v)
    _hist_net = _hist.setdefault("net", deque([0.0] * HIST_LEN, maxlen=HIST_LEN))
    _hist_net.append(dn)

    now = datetime.now()
    date_s = now.strftime("%a %d %b %Y").upper()
    time_s = now.strftime("%H:%M:%S")

    # — header —
    PAD = 16
    draw.text((PAD, 14), "SYS·MONITOR", font=F_HEAD, fill=(*DIM, 255))
    hb = draw.textbbox((0, 0), "SYS·MONITOR ", font=F_HEAD)[2]
    draw.ellipse([PAD + hb, 21, PAD + hb + 9, 30], fill=(*ACCENT, 255))
    draw.text((PAD + hb + 16, 14), "LIVE", font=F_HEAD, fill=(*ACCENT, 255))
    rstr = f"{date_s} · {time_s}"
    rw = draw.textbbox((0, 0), rstr, font=F_HEAD)[2]
    draw.text((WIDTH - PAD - rw, 14), rstr, font=F_HEAD, fill=(*TEXT, 255))

    # — grille —
    top = 52
    gap = 12
    grid_h = HEIGHT - top - PAD
    row_h = (grid_h - gap) / 2
    avail_w = WIDTH - 2 * PAD - 4 * gap
    unit_w = avail_w / 5.5            # 4 colonnes "1fr" + 1 "1.5fr"
    sm_w = unit_w
    net_w = unit_w * 1.5
    xs = [PAD + i * (sm_w + gap) for i in range(4)]
    net_x = PAD + 4 * (sm_w + gap)
    y0 = top
    y1 = top + row_h + gap

    def small(col, row, title, value, unit, hk, pct, vmin=0, vmax=100, color=None):
        x = xs[col]
        y = y0 if row == 0 else y1
        cx, cy, cw, ch = _tile(draw, x, y, sm_w, row_h, title, color or _status_color(pct if pct is not None else 0), pct)
        _big_number(draw, cx, cy + ch, value, unit, color or (_status_color(pct) if pct is not None else ACCENT))
        gw = cw * 0.52
        _graph(draw, cx + cw - gw, cy + ch - 44, gw, 44, _hist[hk],
               color or (_status_color(pct) if pct is not None else ACCENT),
               vmin, vmax)

    small(0, 0, "cpu",  round(cpu),  "%",  "cpu",  cpu)
    small(1, 0, "gpu",  round(gpu),  "%",  "gpu",  gpu)
    small(2, 0, "ram",  round(ram),  "%",  "ram",  ram)
    small(3, 0, "vram", round(vram), "%",  "vram", vram)
    small(0, 1, "cpu°", round(cputemp), "°C", "cputemp", cputemp)
    small(1, 1, "gpu°", round(gputemp), "°C", "gputemp", gputemp)
    small(2, 1, "disk", round(rd + wr), "M",  "disk", disk_act)
    small(3, 1, "clk",  f"{ghz:.1f}", "G",  "freq", min(100, ghz / 5.2 * 100), color=ACCENT)

    # — tuile réseau (2 lignes) —
    cx, cy, cw, ch = _tile(draw, net_x, y0, net_w, row_h * 2 + gap, "network mb/s", ACCENT2)
    draw.text((cx, cy), f"↓ {dn:.1f}", font=F_SUB, fill=(*ACCENT, 255))
    up_s = f"↑ {up:.1f}"
    uw = draw.textbbox((0, 0), up_s, font=F_SUB)[2]
    draw.text((cx + cw - uw, cy), up_s, font=F_SUB, fill=(*ACCENT2, 255))
    gmax = max(10.0, max(_hist_net))
    _graph(draw, cx, cy + 30, cw, ch - 30, _hist_net, ACCENT2, 0, gmax)

    return img


# ── Boucle standalone ────────────────────────────────────────────────────────
def _run_on_panel():
    from panel import Panel
    psutil.cpu_percent(interval=None)          # amorce la mesure
    _rates()                                   # amorce les compteurs
    panel = Panel()
    print("Mode : 04 · TUI tiles — Ctrl+C pour quitter")
    try:
        while True:
            t0 = time.time()
            panel.send_image(build_frame(), fit=False)
            time.sleep(max(0, REFRESH_RATE - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nArrêt")
    finally:
        panel.close()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        psutil.cpu_percent(interval=None)
        _rates()
        time.sleep(0.3)
        for _ in range(12):                    # remplit un peu l'historique
            build_frame()
            time.sleep(0.05)
        out = sys.argv[2]
        build_frame().save(out)
        print(f"Aperçu écrit → {out}")
    else:
        _run_on_panel()
