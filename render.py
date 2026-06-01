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
# LAYOUT : "full" (video), "terminal" (ascii), "clock" (image)
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
    _GPU_OK = "pynvml"
except Exception:
    _GPU_OK = "smi" if __import__("shutil").which("nvidia-smi") else False

def _gpu_stats():
    if _GPU_OK == "pynvml":
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(_gpu)
            mem  = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
            temp = pynvml.nvmlDeviceGetTemperature(_gpu, pynvml.NVML_TEMPERATURE_GPU)
            return float(util.gpu), mem.used / mem.total * 100, temp
        except Exception:
            pass
    if _GPU_OK == "smi":
        try:
            import subprocess
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"], text=True).strip()
            gpu_pct, temp, mem_used, mem_total = map(float, out.split(","))
            return gpu_pct, mem_used / mem_total * 100, int(temp)
        except Exception:
            pass
    return 0.0, 0.0, 0

# ── Fonts ──────────────────────────────────────────────────────────────────
def _font(size):
    _home = pathlib.Path.home()
    candidates = [
        # Windows (user fonts)
        _home / "AppData/Local/Microsoft/Windows/Fonts/Montserrat-SemiBold.ttf",
        _home / "AppData/Local/Microsoft/Windows/Fonts/Montserrat-Medium.ttf",
        pathlib.Path("C:/Windows/Fonts/arialbd.ttf"),
        # Linux
        pathlib.Path("/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf"),
        pathlib.Path("/usr/share/fonts/truetype/montserrat/Montserrat-Medium.ttf"),
        _home / ".fonts/Montserrat-SemiBold.ttf",
        _home / ".fonts/Montserrat-Medium.ttf",
        pathlib.Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except (OSError, TypeError):
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
        pass  # séparateurs supprimés

    elif layout == "full":
        n = len(gauges); slot_w = WIDTH//n; cy = HEIGHT//2-10
        r = min(slot_w//2-30, cy-30)
        for i, g in enumerate(gauges):
            _static_gauge(slot_w*i+slot_w//2, cy, r, g['color'], g['label'], g.get('unit',''))
        pass  # séparateurs supprimés

    _gauge_bg_cache[key] = img
    return img


# ── Build frame ────────────────────────────────────────────────────────────
# Peut être injecté depuis l'extérieur (ex: run_video.py)
background_override: Image.Image | None = None
_smooth: dict = {}  # EMA par métrique

def _ema(key: str, value: float, alpha: float = 0.15) -> float:
    _smooth[key] = value if key not in _smooth else _smooth[key] * (1 - alpha) + value * alpha
    return _smooth[key]

def _draw_terminal_hud(img: Image.Image, gauges: list, now: str) -> Image.Image:
    """HUD style terminal / matrix — barres ASCII sur fond semi-transparent."""
    import math

    MATRIX_GREEN  = (255, 255, 255)
    MATRIX_DIM    = (160, 160, 160)
    BORDER_COLOR  = (220, 220, 220)

    # Code couleur valeurs
    C_NORMAL   = (220, 220, 220)   # blanc/gris — normal
    C_WARM     = (255, 160,  40)   # orange    — chaud  (>60%)
    C_CRITICAL = (180,  60, 255)   # violet    — critique (>85%)

    def _val_color(pct, is_temp=False):
        lo, hi = (65, 82) if is_temp else (60, 85)
        if pct >= hi:   return C_CRITICAL
        if pct >= lo:   return C_WARM
        return C_NORMAL

    def _mono_term(size):
        for p in [
            # Linux
            "/usr/share/fonts/liberation/LiberationMono-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
            # Windows
            "C:/Windows/Fonts/consola.ttf",
            "C:/Windows/Fonts/cour.ttf",
        ]:
            try: return ImageFont.truetype(p, size)
            except OSError: pass
        return _font(size)

    mono     = _mono_term(17)   # barres (taille fixe pour tenir sur la largeur)
    mono_big = _mono_term(28)   # prompt / horloge

    # Largeur de chaque panneau latéral
    PANEL_W = 340
    PAD     = 14
    row_h   = 28
    BAR_LEN = 16

    def _bar(val_pct):
        filled = round(val_pct / 100 * BAR_LEN)
        return "█" * filled + "░" * (BAR_LEN - filled)

    def _draw_bar(x, y, pct, color):
        """Barre dynamique : segments colorés + tip lumineux + fond dim."""
        import time as _t
        filled = round(pct / 100 * BAR_LEN)
        t = _t.time()
        for i in range(BAR_LEN):
            char = "█" if i < filled else "░"
            if i < filled:
                # Pulse : ondulation de brightness sur les segments remplis
                wave = 0.75 + 0.25 * math.sin(t * 3 - i * 0.4)
                c = tuple(int(c * wave) for c in color)
                # Tip : dernier segment plus brillant
                if i == filled - 1:
                    c = tuple(min(255, int(v * 1.4)) for v in color)
                alpha = 255
            else:
                c = (50, 50, 50)
                alpha = 180
            bb = odraw.textbbox((0, 0), char, font=mono)
            cw = bb[2] - bb[0]
            odraw.text((x + i * cw, y), char, font=mono, fill=(*c, alpha))
        # retourne la largeur totale
        bb = odraw.textbbox((0, 0), "█", font=mono)
        return bb[2] - bb[0]

    # Construire les lignes de stats
    metrics = []
    for g in gauges:
        pct = max(0, min(100, (g["value"] - g["vmin"]) / max(1, g["vmax"] - g["vmin"]) * 100))
        if g.get("fmt", ".0f") == ".1f":
            val_str = f"{g['value']:.1f}{g.get('unit','')}"
        else:
            val_str = f"{g['value']:.0f}{g.get('unit','')}"
        is_temp = "°" in g.get("unit", "")
        metrics.append((g["label"], pct, val_str, g["color"], is_temp))

    half = len(metrics) // 2
    left_metrics  = metrics[:half]
    right_metrics = metrics[half:]

    # Fond semi-transparent gauche et droite
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw   = ImageDraw.Draw(overlay, "RGBA")


    import time as _time

    mono_label = _mono_term(26)   # noms
    mono_val   = _mono_term(38)   # valeurs

    ITEM_H = 140  # hauteur par métrique (3 lignes : nom / bar / valeur)

    def _draw_col(mlist, x_base, align_right=False):
        total_h = len(mlist) * ITEM_H
        avail   = HEIGHT - PROMPT_H
        y_start = PROMPT_H + (avail - total_h) // 2
        for i, (label, pct, val_str, color, is_temp) in enumerate(mlist):
            y = y_start + i * ITEM_H
            vc = _val_color(pct, is_temp)

            if align_right:
                bb_bar = odraw.textbbox((0, 0), f"[{'█'*BAR_LEN}]", font=mono)
                bb_val = odraw.textbbox((0, 0), val_str, font=mono_val)
                bb_lbl = odraw.textbbox((0, 0), label,   font=mono_label)
                max_w  = max(bb_bar[2]-bb_bar[0], bb_val[2]-bb_val[0], bb_lbl[2]-bb_lbl[0])
                x = x_base - max_w - PAD
            else:
                x = x_base + PAD

            # Ligne 1 : nom
            odraw.text((x, y),      label,   font=mono_label, fill=(*MATRIX_DIM, 200))
            # Ligne 2 : barre dynamique (couleur selon seuil)
            _draw_bar(x, y + 34, pct, vc)
            # Ligne 3 : valeur (couleur selon seuil)
            odraw.text((x, y + 62), val_str, font=mono_val, fill=(*vc, 255))

    # ── Prompt centré en haut ──
    PROMPT_H = 44  # espace réservé pour la ligne prompt
    prompt = "root@matrix:~$"
    pb  = odraw.textbbox((0, 0), prompt, font=mono_big)
    nb  = odraw.textbbox((0, 0), now,    font=mono_big)
    pw  = pb[2] - pb[0]
    nw  = nb[2] - nb[0]
    gap = 10
    total_w = pw + gap + nw
    px = (WIDTH - total_w) // 2
    py = (PROMPT_H - (pb[3] - pb[1])) // 2
    odraw.text((px,          py), prompt, font=mono_big, fill=(*MATRIX_DIM, 200))
    odraw.text((px + pw + gap, py), now,  font=mono_big, fill=(*MATRIX_GREEN, 230))
    # curseur clignotant
    cursor_on = int(_time.time() * 2) % 2 == 0
    if cursor_on:
        cx = px + pw + gap + nw + 6
        ch_h = pb[3] - pb[1]
        odraw.rectangle([cx, py + 2, cx + 12, py + ch_h], fill=(*MATRIX_GREEN, 230))

    _draw_col(left_metrics,  x_base=0,     align_right=False)
    _draw_col(right_metrics, x_base=WIDTH, align_right=True)

    result = img.convert("RGBA")
    result.alpha_composite(overlay)
    return result.convert("RGB")


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

    if LAYOUT == "terminal":
        return _draw_terminal_hud(img, gauges, now)

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
