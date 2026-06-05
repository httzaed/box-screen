"""
Dashboard — cadrans circulaires style tableau de bord
Modifie ce fichier, Ctrl+S → mise à jour live
"""

import json
import pathlib
import colorsys
import math
import threading
import time
from datetime import datetime

import psutil
from PIL import Image, ImageDraw, ImageFont

# ── Dimensions ─────────────────────────────────────────────────────────────
# LAYOUT : "full" (video), "terminal" (ascii), "clock" (image), "lyrics" (art + lyrics)
LAYOUT = "lyrics"

WIDTH  = 1920
HEIGHT = 462
REFRESH_RATE = 1.0

# ── Fond image pour les lyrics (lines.jpg) ──────────────────────────────────
_LYRICS_BG_PATH = pathlib.Path(__file__).parent / "lines.jpg"
_lyrics_bg_cache: "Image.Image | None" = None

def _get_lyrics_bg() -> "Image.Image | None":
    """Charge lines.jpg, recadré en cover sur WIDTH×HEIGHT (RGBA), mis en cache."""
    global _lyrics_bg_cache
    if _lyrics_bg_cache is not None:
        return _lyrics_bg_cache
    try:
        src = Image.open(_LYRICS_BG_PATH).convert("RGBA")
    except Exception as e:
        print(f"[RENDER] lines.jpg introuvable: {e}")
        return None
    # Cover: redimensionne en gardant le ratio puis recadre au centre
    sw, sh = src.size
    scale = max(WIDTH / sw, HEIGHT / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    src = src.resize((nw, nh), Image.LANCZOS)
    left = (nw - WIDTH) // 2
    top  = (nh - HEIGHT) // 2
    _lyrics_bg_cache = src.crop((left, top, left + WIDTH, top + HEIGHT))
    return _lyrics_bg_cache

# ── Palette (depuis palette.json si disponible) ────────────────────────────
_p = pathlib.Path(__file__).parent / "palette.json"
try:
    _pal         = json.loads(_p.read_text())
    _c           = _pal.get("colors", {})
    COLOR_TEXT   = tuple(_pal["text"][0])
    COLOR_CPU    = tuple(_c.get("cpu",  _pal["indicator"][0]))
    COLOR_RAM    = tuple(_c.get("ram",  _pal["indicator"][1]))
    COLOR_GPU    = tuple(_c.get("gpu",  _pal["indicator"][2] if len(_pal["indicator"]) > 2 else [30, 200, 255]))
    COLOR_ACCENT = tuple(_c.get("temp", _pal.get("accent", [210, 20, 20])))
    COLOR_NET    = tuple(_c.get("net",  [0, 210, 130]))
    COLOR_DISK   = tuple(_c.get("disk", [255, 200, 0]))
    _C_WARM_PAL  = tuple(_c.get("warm",     [255, 120,  0]))
    _C_CRIT_PAL  = tuple(_c.get("critical", [180,  40, 255]))
except Exception:
    COLOR_TEXT   = (220, 215, 205)
    COLOR_CPU    = (130,  55, 255)
    COLOR_RAM    = (255, 100,   0)
    COLOR_GPU    = ( 30, 200, 255)
    COLOR_ACCENT = (210,  20,  20)
    COLOR_NET    = (  0, 210, 130)
    COLOR_DISK   = (255, 200,   0)
    _C_WARM_PAL  = (255, 120,   0)
    _C_CRIT_PAL  = (180,  40, 255)

COLOR_BG    = ( 12,  12,  18)
COLOR_TRACK = ( 35,  35,  50)

# ── Couleur LED → RGB ──────────────────────────────────────────────────────
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
    """Lit case_color depuis led_config.json → RGB. Fallback violet."""
    try:
        cfg = json.loads((pathlib.Path(__file__).parent / "led_config.json").read_text())
        return _LED_RGB.get(cfg.get("case_color", "violet"), (180, 40, 255))
    except Exception:
        return (180, 40, 255)

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

FONT_VALUE    = _font(52)
FONT_LABEL    = _font(22)
FONT_UNIT     = _font(20)
FONT_CLOCK    = _font(44)
FONT_CLOCK_BIG = _font(340)

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

# ── Cache frame écran ────────────────────────────────────────────────────────
_frame_cache: dict = {"last_key": None, "last_frame": None}
_frame_lock = threading.Lock() if threading else None

def _get_frame_key() -> str:
    """Génère une clé unique représentant l'état actuel à afficher."""
    # Pour lyrics: artist + title + current_line_idx + position_tronquée
    try:
        import lyrics_source as _ls
        state = _ls.get_state()
        if state and state.get("lines"):
            idx = state.get("current_idx", 0)
            line = state["lines"][idx][1] if idx < len(state["lines"]) else ""
            # Ajoute la position (tronquée à 2 secondes près) pour éviter le cache statique
            pos = state.get("position", 0)
            pos_bucket = int(pos // 2) if pos else 0
            return f"lyrics:{state.get('artist','')}|{state.get('title','')}|{idx}|{pos_bucket}"
    except:
        pass

    # Pour autres layouts: key basée sur les gauges + temps
    now_second = int(datetime.now().strftime("%S"))
    return f"{LAYOUT}|{now_second}"


def _ema(key: str, value: float, alpha: float = 0.15) -> float:
    _smooth[key] = value if key not in _smooth else _smooth[key] * (1 - alpha) + value * alpha
    return _smooth[key]

def _draw_terminal_hud(img: Image.Image, gauges: list, now: str) -> Image.Image:
    """HUD style terminal / matrix — barres ASCII sur fond semi-transparent."""
    import math

    MATRIX_GREEN  = (255, 255, 255)
    MATRIX_DIM    = (160, 160, 160)
    BORDER_COLOR  = (220, 220, 220)

    # Code couleur valeurs : normal blanc → haut = LED case → critique violet
    C_NORMAL   = (220, 220, 220)   # blanc/gris — normal
    C_WARM     = _led_accent_color()  # couleur LED case — chaud  (>60%)
    C_CRITICAL = _C_CRIT_PAL          # violet           — critique (>85%)

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


# ── Animated Abstract Art ────────────────────────────────────────────────────────────
# Pas de cache - animation fluide frame par frame

def _animated_abstract_art(t: float) -> Image.Image:
    """
    Artwork animé continu - formes qui bougent fluidement avec le temps.
    t = temps en secondes (time.monotonic)
    """
    art = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art, "RGBA")

    # Couleurs de la palette - BEAUCOUP plus visibles
    accent = _led_accent_color()
    c_accent = (*accent, 100)
    c_warm = (*_C_WARM_PAL[:3], 80)
    c_crit = (*_C_CRIT_PAL[:3], 70)
    c_track = (*COLOR_TRACK[:3], 60)

    # ── Vagues onduleuses en bas (fluides) ───────────────────────────────────────
    wave_base_y = HEIGHT * 0.75
    for layer in range(3):
        wave_y = wave_base_y + layer * 25
        alpha = max(40, 120 - layer * 25)
        color = (*accent[:3], alpha)

        prev_x, prev_y = None, None
        for x in range(0, WIDTH + 20, 20):
            offset = math.sin((x + t * (50 + layer * 20)) * (0.015 + layer * 0.005)) * (20 - layer * 5)
            y = wave_y + offset
            if prev_x is not None:
                draw.line([(prev_x, prev_y), (x, y)], fill=color, width=3)
            prev_x, prev_y = x, y

    # ── Cercles qui pulsent ─────────────────────────────────────────────────────────
    for i in range(5):
        cx = WIDTH * (0.15 + i * 0.175) + math.sin(t * 0.3 + i) * 40
        cy = HEIGHT * 0.4 + math.cos(t * 0.4 + i * 2) * 60

        pulse = math.sin(t * 2 + i) * 0.5 + 0.5
        base_r = 30 + i * 15
        r = base_r + pulse * 20

        alpha = int(80 + pulse * 60)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                    outline=(*accent[:3], alpha), width=4)

    # ── Lignes diagonales qui défilent ─────────────────────────────────────────────
    for i in range(7):
        offset = (t * (30 + i * 10) + i * 100) % (WIDTH + HEIGHT) - HEIGHT
        for x in range(0, WIDTH + 1, 80):
            y = x * 0.3 + offset
            if 0 <= y <= HEIGHT:
                alpha = max(30, 100 - abs(x - WIDTH//2) // 40)
                draw.line([(x, y), (x + 40, y + 12)], fill=c_warm, width=2)

    # ── Formes triangulaires qui tournent ───────────────────────────────────────────
    for i in range(3):
        tx = WIDTH * (0.2 + i * 0.3)
        ty = HEIGHT * 0.25 + math.sin(t + i * 2) * 40

        angle = t * (0.5 + i * 0.2) + i * 2
        size = 35 + i * 10

        points = []
        for j in range(3):
            a = angle + j * (2 * math.pi / 3)
            px = tx + math.cos(a) * size
            py = ty + math.sin(a) * size
            points.append((px, py))

        for j in range(3):
            p1 = points[j]
            p2 = points[(j + 1) % 3]
            draw.line([p1, p2], fill=(*COLOR_ACCENT[:3], 90), width=3)

    # ── Particules flottantes ────────────────────────────────────────────────────────
    for i in range(12):
        px = (i * 167) % WIDTH
        py = (HEIGHT * 0.9) - ((t * 20 + i * 50) % (HEIGHT * 1.2))

        size = 5 + math.sin(t + i) * 3
        alpha = max(40, int(100 - py / HEIGHT * 40))
        draw.ellipse([px - size, py - size, px + size, py + size],
                    fill=(*_C_CRIT_PAL[:3], alpha))

    # ── Arcs concentriques qui respirent ────────────────────────────────────────────
    arc_cx = WIDTH * 0.7
    arc_cy = HEIGHT * 0.5
    for i in range(4):
        breath = math.sin(t * 1.5 + i * 0.5) * 0.3 + 0.7
        r = (40 + i * 25) * breath

        start_angle = t * 20 + i * 45
        extent = 90 + i * 30

        draw.arc([arc_cx - r, arc_cy - r, arc_cx + r, arc_cy + r],
                start_angle, start_angle + extent,
                fill=(*accent[:3], max(40, 100 - i * 15)), width=4)

    # ── Lien vertical subtil ────────────────────────────────────────────────────────
    x_line = WIDTH * 0.3 + math.sin(t * 0.5) * 100
    draw.line([(x_line, 0), (x_line, HEIGHT)], fill=c_track, width=2)

    return art


# ── Lyrics HUD ────────────────────────────────────────────────────────────────
_FONT_LYRIC_BIG  = _font(76)
_FONT_LYRIC_MED  = _font(48)
_FONT_LYRIC_SM   = _font(32)
_FONT_LYRIC_META = _font(26)
_FONT_LYRIC_TINY = _font(18)

def _draw_lyrics_hud(img: Image.Image) -> Image.Image:
    """
    Mode Karaoke Flow : défilement fluide synchronisé au temps, design épuré.
    """
    # Static variable pour throttler les messages de debug
    if not hasattr(_draw_lyrics_hud, '_last_debug_print'):
        _draw_lyrics_hud._last_debug_print = 0

    try:
        import lyrics_source as _ls
        import time
        state = _ls.get_state()
    except ImportError:
        state = None

    # ── SYNC OFFSET (ajuste ici si les lyrics sont en avance ou retard) ───────────
    # positif = retarder les lyrics, négatif = les avancer
    SYNC_OFFSET = 0.5  # secondes (réduit - 1.5 était trop pour certaines chansons)

    # ── Art animé - toujours visible, plus intense quand blank ─────────────────────
    time_now = time.monotonic()
    is_blank = state is None or not state["lines"]

    # Fond overlay
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))

    if is_blank:
        # MODE BLANK: artwork plein écran, bien visible
        art_layer = _animated_abstract_art(time_now)
        overlay.alpha_composite(art_layer)
        od = ImageDraw.Draw(overlay)
        # Pas de texte, juste l'art qui bouge
    else:
        # MODE LYRICS: fond lines.jpg (fallback art animé) assombri + lyrics
        bg = _get_lyrics_bg()
        if bg is not None:
            bg_layer = bg.copy()
        else:
            bg_layer = _animated_abstract_art(time_now)
        # Assombrit le fond pour la lisibilité du texte
        dim_overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 180))
        bg_layer.alpha_composite(dim_overlay)
        overlay.alpha_composite(bg_layer)
        od = ImageDraw.Draw(overlay)

    CY = HEIGHT // 2
    LINE_SPACING = 70
    VISIBLE_RANGE = 3  # lignes avant/après

    if state is None or not state["lines"]:
        msg = state.get("title", "") if state else "♪ Aucune lecture"
        sub = state.get("artist", "") if state else "Lance une musique"

        # ÉTAT DE CHARGEMENT
        is_loading = state.get("loading", False) if state else False
        if is_loading:
            # Affichage "Recherche en cours..."
            msg = "♪ Recherche..."
            sub = f"{state.get('artist', '')} — {state.get('title', '')}" if state else ""

        # Simple et clean
        bbox = od.textbbox((0, 0), msg, font=_FONT_LYRIC_MED)
        tw = bbox[2] - bbox[0]
        od.text((WIDTH//2 - tw//2, CY - 20), msg, font=_FONT_LYRIC_MED,
                fill=(*COLOR_TEXT[:3], 180))
        if sub:
            bbox2 = od.textbbox((0, 0), sub, font=_FONT_LYRIC_META)
            tw2 = bbox2[2] - bbox2[0]
            od.text((WIDTH//2 - tw2//2, CY + 35), sub, font=_FONT_LYRIC_META,
                    fill=(*COLOR_TEXT[:3], 100))

    else:
        lines = state["lines"]
        position = state.get("position")  # position exacte en secondes
        idx = state.get("current_idx", 0)
        artist = state.get("artist", "")
        title = state.get("title", "")
        has_sync = state.get("has_sync", False)

        # ── Trouve la ligne active basée sur position exacte ─────────────────────
        display_idx = idx  # ligne à centrer (par défaut l'idx du state)

        # DEBUG - toutes les 10s seulement
        now = time.monotonic()
        if now - _draw_lyrics_hud._last_debug_print >= 10:
            print(f"[RENDER] lyrics: pos={position:.1f}s, idx={idx}, lines={len(lines)}, has_sync={has_sync}")
            _draw_lyrics_hud._last_debug_print = now

        # Si on a position exacte + sync, utilise-la pour trouver la bonne ligne
        if has_sync and position is not None:
            # Offset personnalisé depuis le cache, ou fallback
            try:
                import lyrics_source as _ls
                custom_offset = _ls.get_song_offset(artist, title)
                sync_pos = position + (custom_offset if custom_offset is not None else SYNC_OFFSET)
            except:
                sync_pos = position + SYNC_OFFSET

            # Trouve la ligne exacte basée sur le timestamp ajusté
            best_i = 0
            for i, (ts, _) in enumerate(lines):
                if ts is not None and ts <= sync_pos:
                    best_i = i
                elif ts is not None and ts > sync_pos:
                    break
            display_idx = best_i

            # DEBUG - utilise le même timer
            if now - _draw_lyrics_hud._last_debug_print >= 10:
                print(f"[RENDER] sync: sync_pos={sync_pos:.1f}s, display_idx={display_idx}, line={lines[display_idx][1] if display_idx < len(lines) else 'N/A'}")

        # Fallback pour plain lyrics (pas de timestamps) - avance toutes les 4 secondes
        elif not has_sync and position is not None and len(lines) > 0:
            # Estime: ~4-5 secondes par ligne en moyenne
            plain_idx = int(position / 4.5) % len(lines)
            display_idx = plain_idx

            # DEBUG - utilise le même timer
            if now - _draw_lyrics_hud._last_debug_print >= 10:
                print(f"[RENDER] plain mode: pos={position:.1f}s, display_idx={display_idx}/{len(lines)}")

        # ── Meta en haut (subtil) ─────────────────────────────────────────────────
        meta = f"{artist}" if artist else title
        mbbox = od.textbbox((0, 0), meta, font=_FONT_LYRIC_META)
        mw = mbbox[2] - mbbox[0]
        display_meta = meta
        while mw > WIDTH - 80 and len(display_meta) > 8:
            display_meta = display_meta[:-8] + "…"
            mbbox = od.textbbox((0, 0), display_meta, font=_FONT_LYRIC_META)
            mw = mbbox[2] - mbbox[0]
        od.text((WIDTH//2 - mw//2, 18), display_meta,
                font=_FONT_LYRIC_META, fill=(*COLOR_ACCENT[:3], 120))

        # ── Dessine les lignes ─────────────────────────────────────────────────────
        def _draw_line(text, font, cy_offset, alpha, scale=1.0, glow=False):
            if not text:
                return
            # Scale la police si besoin
            if scale != 1.0:
                try:
                    font = _font(int(font.size * scale))
                except:
                    pass

            bbox = od.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            # Tronque
            display = text
            while tw > WIDTH - 100 and len(display) > 4:
                display = display[:-4] + "…"
                bbox = od.textbbox((0, 0), display, font=font)
                tw = bbox[2] - bbox[0]

            bh = bbox[3] - bbox[1]
            x = WIDTH // 2 - tw // 2
            y = CY + cy_offset - bh // 2

            if glow:
                # Glow subtil multi-couche
                for g in range(6, 0, -2):
                    od.text((x + g//2, y + g//2), display, font=font,
                            fill=(*COLOR_ACCENT[:3], int(alpha * 0.08 * g)))
                # Ombre
                od.text((x + 3, y + 3), display, font=font, fill=(0, 0, 0, 100))

            od.text((x, y), display, font=font, fill=(*COLOR_TEXT[:3], int(alpha)))

        # Lignes visibles : de -VISIBLE_RANGE à +VISIBLE_RANGE
        for offset in range(-VISIBLE_RANGE, VISIBLE_RANGE + 1):
            line_idx = display_idx + offset
            if 0 <= line_idx < len(lines):
                text = lines[line_idx][1]
                base_y = offset * LINE_SPACING  # position fixe, pas d'animation

                if offset == 0:
                    # Ligne courante - GRANDE, brillante
                    _draw_line(text, _FONT_LYRIC_BIG, base_y, 250, scale=1.0, glow=True)
                elif abs(offset) == 1:
                    # Adjacentes - moyennes
                    _draw_line(text, _FONT_LYRIC_MED, base_y, 140, scale=0.75)
                else:
                    # Lointaines - petites, discrètes
                    _draw_line(text, _FONT_LYRIC_SM, base_y, 60, scale=0.6)

        # ── Progression discrète en bas ───────────────────────────────────────────
        if has_sync and len(lines) > 1:
            first_ts = lines[0][0] if lines[0][0] is not None else 0
            last_ts = lines[-1][0] if lines[-1][0] is not None else 0
            if position and last_ts > first_ts:
                sync_pos = position + SYNC_OFFSET
                prog = max(0, min(1, (sync_pos - first_ts) / (last_ts - first_ts)))
                bar_w = 200
                bx = WIDTH//2 - bar_w//2
                by = HEIGHT - 16
                # Fond
                od.rectangle([bx, by, bx + bar_w, by + 2], fill=(30, 30, 40, 200))
                # Fill
                od.rectangle([bx, by, bx + int(bar_w * prog), by + 2],
                            fill=(*COLOR_ACCENT[:3], 220))

    result = img.convert("RGBA")
    result.alpha_composite(overlay)
    return result.convert("RGB")


def build_frame() -> Image.Image:
    # ── Check cache frame ────────────────────────────────────────────────────
    current_key = _get_frame_key()
    if LAYOUT == "lyrics":  # cache intelligent pour lyrics
        with _frame_lock:
            if _frame_cache.get("last_key") == current_key and _frame_cache.get("last_frame") is not None:
                return _frame_cache["last_frame"].copy()

    if background_override is not None:
        img = background_override.copy()
    else:
        img  = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(img, "RGBA")

    cpu_freq = psutil.cpu_freq()
    cpu_max  = cpu_freq.max / 1000 if cpu_freq else 5.0
    ram      = psutil.virtual_memory()
    gpu_pct_raw, gpu_vram_pct_raw, gpu_temp_raw = _gpu_stats()

    cpu_pct      = _ema("cpu",      psutil.cpu_percent(interval=None))
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

    if LAYOUT == "lyrics":
        return _draw_lyrics_hud(img)

    if LAYOUT == "clock":
        bbox_c = draw.textbbox((0, 0), now, font=FONT_CLOCK_BIG)
        cw = bbox_c[2]-bbox_c[0]; ch = bbox_c[3]-bbox_c[1]
        x = WIDTH//2 - cw//2 - bbox_c[0]
        y = HEIGHT//2 - ch//2 - bbox_c[1]
        overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.text((x+4, y+4), now, font=FONT_CLOCK_BIG, fill=(0, 0, 0, 80))
        od.text((x, y), now, font=FONT_CLOCK_BIG, fill=(*COLOR_TEXT[:3], 140))
        img = img.convert("RGBA")
        img.alpha_composite(overlay)
        img = img.convert("RGB")

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

    # ── Save in cache ────────────────────────────────────────────────────────
    if LAYOUT == "lyrics":
        with _frame_lock:
            _frame_cache["last_key"] = current_key
            _frame_cache["last_frame"] = img.copy()

    return img
