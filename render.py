"""
Dashboard — cadrans circulaires style tableau de bord
Modifie ce fichier, Ctrl+S → mise à jour live
"""

import json
import pathlib
import colorsys
from datetime import datetime

import psutil
from PIL import Image, ImageDraw, ImageFont

# ── Dimensions ─────────────────────────────────────────────────────────────
# LAYOUT : "full" (video), "split" (ascii), "clock" (image)
LAYOUT = "full"

WIDTH  = 1920
HEIGHT = 462
REFRESH_RATE = 1.0

# ── Palette (depuis palette.json si disponible) ────────────────────────────
_p = pathlib.Path(__file__).parent / "palette.json"
try:
    _pal         = json.loads(_p.read_text())
    COLOR_TEXT   = tuple(_pal["text"][0])
    COLOR_CPU    = tuple(_pal["indicator"][0])
    COLOR_RAM    = tuple(_pal["indicator"][1])
    COLOR_GPU    = tuple(_pal["indicator"][2]) if len(_pal["indicator"]) > 2 else (77, 220, 255)
    COLOR_ACCENT = tuple(_pal.get("accent", [255, 80, 80]))
except Exception:
    COLOR_TEXT   = (230, 220, 200)
    COLOR_CPU    = (140,  82, 255)
    COLOR_RAM    = (255, 145,  77)
    COLOR_GPU    = ( 77, 220, 255)
    COLOR_ACCENT = (255,  80,  80)

COLOR_BG    = ( 12,  12,  18)
COLOR_TRACK = ( 35,  35,  50)

# ── GPU ────────────────────────────────────────────────────────────────────
try:
    import pynvml
    pynvml.nvmlInit()
    _gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_OK = True
except Exception:
    _GPU_OK = False

def _gpu_stats():
    if not _GPU_OK:
        return 0.0, 0.0, 0
    try:
        util = pynvml.nvmlDeviceGetUtilizationRates(_gpu)
        mem  = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
        temp = pynvml.nvmlDeviceGetTemperature(_gpu, pynvml.NVML_TEMPERATURE_GPU)
        return float(util.gpu), mem.used / mem.total * 100, temp
    except Exception:
        return 0.0, 0.0, 0

# ── Fonts ──────────────────────────────────────────────────────────────────
def _font(size):
    for path in [
        r"C:\Users\hedik\AppData\Local\Microsoft\Windows\Fonts\Montserrat-SemiBold.ttf",
        r"C:\Users\hedik\AppData\Local\Microsoft\Windows\Fonts\Montserrat-Medium.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()

FONT_VALUE = _font(52)
FONT_LABEL = _font(22)
FONT_UNIT  = _font(20)
FONT_CLOCK = _font(44)

# ── Gauge ──────────────────────────────────────────────────────────────────
# Arc de 240° : de 150° à 390° (sens horaire PIL)
_ARC_START = 150
_ARC_TOTAL = 240
_ARC_WIDTH = 18
_TRACK_W   = 6

def _draw_gauge(draw, cx, cy, r, value, vmin, vmax, color, label, unit="", fmt=".0f"):
    """Dessine un cadran arc style speedometer."""
    pct   = max(0, min(1, (value - vmin) / (vmax - vmin)))
    end   = _ARC_START + pct * _ARC_TOTAL
    bbox  = [cx - r, cy - r, cx + r, cy + r]

    # Track (fond de l'arc)
    draw.arc(bbox, _ARC_START, _ARC_START + _ARC_TOTAL,
             fill=COLOR_TRACK, width=_TRACK_W)

    # Arc rempli
    if pct > 0.01:
        draw.arc(bbox, _ARC_START, end, fill=color, width=_ARC_WIDTH)

    # Point terminal (cap arrondi simulé)
    import math
    angle_rad = math.radians(end)
    px = cx + (r) * math.cos(angle_rad)
    py = cy + (r) * math.sin(angle_rad)
    hw = _ARC_WIDTH // 2
    draw.ellipse([px-hw, py-hw, px+hw, py+hw], fill=color)

    # Halo cercle fixe derrière la valeur
    hr = 58
    draw.ellipse([cx-hr, cy-hr, cx+hr, cy+hr], fill=(0, 0, 0, 170))

    val_str = f"{value:{fmt}}"
    bbox_v  = draw.textbbox((0, 0), val_str, font=FONT_VALUE)
    tw = bbox_v[2] - bbox_v[0]
    draw.text((cx - tw // 2, cy - 34), val_str, font=FONT_VALUE, fill=color)

    # Unité
    if unit:
        bbox_u = draw.textbbox((0, 0), unit, font=FONT_UNIT)
        uw = bbox_u[2] - bbox_u[0]
        draw.text((cx - uw // 2, cy + 22), unit, font=FONT_UNIT, fill=(*color[:3], 160))

    # Label sous le cadran
    bbox_l = draw.textbbox((0, 0), label, font=FONT_LABEL)
    lw = bbox_l[2] - bbox_l[0]
    draw.text((cx - lw // 2, cy + r + 8), label, font=FONT_LABEL, fill=COLOR_TEXT)


# ── Cache du fond statique des gauges ─────────────────────────────────────
_gauge_bg_cache: dict = {}   # key → Image

def _get_gauge_bg(layout: str, gauges: list) -> Image.Image:
    """Pré-rend tracks, halos, labels, séparateurs — éléments qui ne bougent pas."""
    key = (layout, tuple(g['label'] for g in gauges))
    if key in _gauge_bg_cache:
        return _gauge_bg_cache[key]

    img  = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    def _static_gauge(cx, cy, r, color, label, unit=""):
        bbox = [cx-r, cy-r, cx+r, cy+r]
        draw.arc(bbox, _ARC_START, _ARC_START+_ARC_TOTAL, fill=COLOR_TRACK, width=_TRACK_W)
        hr = 58
        draw.ellipse([cx-hr, cy-hr, cx+hr, cy+hr], fill=(0, 0, 0, 170))
        if unit:
            bbox_u = draw.textbbox((0,0), unit, font=FONT_UNIT)
            uw = bbox_u[2]-bbox_u[0]
            draw.text((cx-uw//2, cy+22), unit, font=FONT_UNIT, fill=(*color[:3], 100))
        bbox_l = draw.textbbox((0,0), label, font=FONT_LABEL)
        lw = bbox_l[2]-bbox_l[0]
        draw.text((cx-lw//2, cy+r+8), label, font=FONT_LABEL, fill=COLOR_TEXT)

    if layout == "split":
        left_g = gauges[:3]; right_g = gauges[3:]
        quarter = WIDTH//4; n_rows = 3
        slot_h = HEIGHT//n_rows; r = min(quarter//2-16, slot_h//2-20)
        for i, g in enumerate(left_g):
            _static_gauge(quarter//2, slot_h*i+slot_h//2, r, g['color'], g['label'], g.get('unit',''))
        for i, g in enumerate(right_g):
            _static_gauge(WIDTH-quarter//2, slot_h*i+slot_h//2, r, g['color'], g['label'], g.get('unit',''))
        draw.line([(quarter,10),(quarter,HEIGHT-10)], fill=(*COLOR_TRACK,120), width=1)
        draw.line([(WIDTH-quarter,10),(WIDTH-quarter,HEIGHT-10)], fill=(*COLOR_TRACK,120), width=1)

    elif layout == "full":
        n = len(gauges); slot_w = WIDTH//n; cy = HEIGHT//2-10
        r = min(slot_w//2-30, cy-30)
        for i, g in enumerate(gauges):
            _static_gauge(slot_w*i+slot_w//2, cy, r, g['color'], g['label'], g.get('unit',''))
        for i in range(1, n):
            draw.line([(slot_w*i,20),(slot_w*i,HEIGHT-20)], fill=(*COLOR_TRACK,180), width=1)

    _gauge_bg_cache[key] = img
    return img


# ── Build frame ────────────────────────────────────────────────────────────
# Peut être injecté depuis l'extérieur (ex: run_video.py)
background_override: Image.Image | None = None
_smooth: dict = {}  # EMA par métrique

def _ema(key: str, value: float, alpha: float = 0.15) -> float:
    _smooth[key] = value if key not in _smooth else _smooth[key] * (1 - alpha) + value * alpha
    return _smooth[key]

def build_frame() -> Image.Image:
    if background_override is not None:
        img = background_override.copy()
    else:
        img  = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(img, "RGBA")

    cpu_freq = psutil.cpu_freq()
    cpu_max  = cpu_freq.max / 1000 if cpu_freq else 5.0
    ram      = psutil.virtual_memory()
    gpu_pct_raw, gpu_vram_pct_raw, gpu_temp_raw = _gpu_stats()

    cpu_pct      = _ema("cpu",      psutil.cpu_percent(interval=0.2))
    cpu_ghz      = _ema("freq",     cpu_freq.current / 1000 if cpu_freq else 0)
    gpu_pct      = _ema("gpu",      gpu_pct_raw)
    gpu_vram_pct = _ema("vram",     gpu_vram_pct_raw)
    gpu_temp     = _ema("gputemp",  gpu_temp_raw)

    # Invalide le cache si la palette a changé (hot reload)
    if _gauge_bg_cache and getattr(build_frame, '_last_layout', None) != LAYOUT:
        _gauge_bg_cache.clear()
    build_frame._last_layout = LAYOUT

    # 6 cadrans répartis sur 1920px
    # CPU Usage | CPU Freq | RAM | GPU Usage | GPU Temp | GPU VRAM
    gauges = [
        dict(value=cpu_pct,      vmin=0,   vmax=100,   color=COLOR_CPU,    label="CPU",      unit="%"),
        dict(value=cpu_ghz,      vmin=0,   vmax=cpu_max, color=COLOR_CPU,  label="FRÉQ",     unit="GHz", fmt=".1f"),
        dict(value=ram.percent,  vmin=0,   vmax=100,   color=COLOR_RAM,    label="RAM",      unit="%"),
        dict(value=gpu_pct,      vmin=0,   vmax=100,   color=COLOR_GPU,    label="GPU",      unit="%"),
        dict(value=gpu_temp,     vmin=0,   vmax=100,   color=COLOR_ACCENT, label="GPU TEMP", unit="°C"),
        dict(value=gpu_vram_pct, vmin=0,   vmax=100,   color=COLOR_GPU,    label="VRAM",     unit="%"),
    ]

    now = datetime.now().strftime("%H:%M:%S")

    if LAYOUT == "clock":
        bbox_c = draw.textbbox((0, 0), now, font=FONT_CLOCK)
        cw = bbox_c[2]-bbox_c[0]; ch = bbox_c[3]-bbox_c[1]
        draw.text((WIDTH//2-cw//2, HEIGHT//2-ch//2), now, font=FONT_CLOCK, fill=COLOR_TEXT)

    else:
        # Composite le fond statique (tracks, halos, labels)
        static_bg = _get_gauge_bg(LAYOUT, gauges)
        img.paste(static_bg, (0, 0), static_bg)
        draw = ImageDraw.Draw(img, "RGBA")  # refresh draw après paste

        if LAYOUT == "split":
            quarter = WIDTH//4; slot_h = HEIGHT//3
            r = min(quarter//2-16, slot_h//2-20)
            pairs = [(gauges[:3], quarter//2), (gauges[3:], WIDTH-quarter//2)]
            for glist, cx in pairs:
                for i, g in enumerate(glist):
                    cy = slot_h*i+slot_h//2
                    _draw_gauge(draw, cx, cy, r, **g)
            bbox_c = draw.textbbox((0,0), now, font=FONT_CLOCK)
            cw = bbox_c[2]-bbox_c[0]
            draw.text((WIDTH//2-cw//2, HEIGHT-54), now, font=FONT_CLOCK, fill=(*COLOR_TEXT[:3], 120))

        else:  # full
            n = len(gauges); slot_w = WIDTH//n; cy = HEIGHT//2-10
            r = min(slot_w//2-30, cy-30)
            for i, g in enumerate(gauges):
                _draw_gauge(draw, slot_w*i+slot_w//2, cy, r, **g)
            bbox_c = draw.textbbox((0,0), now, font=FONT_CLOCK)
            cw = bbox_c[2]-bbox_c[0]
            draw.text((WIDTH//2-cw//2, HEIGHT-54), now, font=FONT_CLOCK, fill=(*COLOR_TEXT[:3], 120))

    return img
