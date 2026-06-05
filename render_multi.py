"""
render_multi.py — Box Screen HUD mode "multi"
═══════════════════════════════════════════════════════════════════════════
Multi-visualizer HUD style that displays multiple visualizers in a grid:
  • Top row: Mini circular gauges (CPU, RAM, GPU)
  • Middle: Time + beat indicator + network graph
  • Bottom: History graphs (line/block style)
  • Side accents: LED-reactive borders based on audio level

Usage in launcher.py:
  import render_multi
  panel.send_image(render_multi.build_frame())       # default dark bg
  panel.send_image(render_multi.build_frame(bg=img)) # composite over content
"""

import pathlib
import json
import math
import time
from collections import deque
from datetime import datetime

import psutil
from PIL import Image, ImageDraw, ImageFont

# ── Dimensions / cadence ───────────────────────────────────────────────────
WIDTH       = 1920
HEIGHT      = 462
REFRESH_RATE = 0.15          # ~6-7 FPS for smooth animation

HIST_LEN    = 180            # samples for history graphs

# ── Palette ──────────────────────────────────────────────────────────────
BG_TOP     = (12,  10,  20)
BG_BOT     = (6,    4,  12)
COLOR_TEXT = (220, 215, 205)
COLOR_DIM  = (120, 115, 125)
COLOR_ACCENT = (200, 64, 255)

LED_COLORS = {
    "violet": (180,  40, 255),
    "cyan":   ( 30, 200, 255),
    "blue":   (  0,  80, 255),
    "teal":   ( ( 0, 210, 150),
    "green":  (  0, 212, 130),
    "yellow": (255, 200,   0),
    "orange": (255,  85,   0),
    "red":    (255,   0,   0),
    "pink":   (255,  20, 100),
    "white":  (220, 220, 220),
}

# ── Load from palette.json if available ──────────────────────────────────
_p = pathlib.Path(__file__).parent / "palette.json"
try:
    _pal = json.loads(_p.read_text())
    if _pal.get("accent"):
        COLOR_ACCENT = tuple(_pal["accent"][:3])
except Exception:
    pass

def _led_accent_color() -> tuple:
    """Lit case_color depuis led_config.json → RGB. Fallback violet."""
    try:
        cfg = json.loads((pathlib.Path(__file__).parent / "led_config.json").read_text())
        return LED_COLORS.get(cfg.get("case_color", "violet"), (180, 40, 255))
    except Exception:
        return (180, 40, 255)

# ── Colors for gauges ─────────────────────────────────────────────────────
COLOR_CPU  = (130,  55, 255)
COLOR_RAM  = (255, 100,   0)
COLOR_GPU  = ( 30, 200, 255)
COLOR_NET  = (  0, 210, 130)
COLOR_WARN = (255, 170,   0)
COLOR_CRIT = (255,  40,  80)

# ── Fonts ─────────────────────────────────────────────────────────────────
def _font(size, bold=False):
    home = pathlib.Path.home()
    candidates = [
        # Windows
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf" if bold else "/usr/share/fonts/truetype/montserrat/Montserrat-Medium.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, TypeError):
            pass
    return ImageFont.load_default()

F_TINY    = _font(14)
F_SMALL   = _font(18)
F_MEDIUM  = _font(24)
F_LARGE   = _font(36, bold=True)
F_HUGE    = _font(72, bold=True)

# ── GPU detection ────────────────────────────────────────────────────────
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
            mem = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
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

# ── State / History ──────────────────────────────────────────────────────
_hist = {
    "cpu": deque([0.0] * HIST_LEN, maxlen=HIST_LEN),
    "ram": deque([0.0] * HIST_LEN, maxlen=HIST_LEN),
    "gpu": deque([0.0] * HIST_LEN, maxlen=HIST_LEN),
    "net": deque([0.0] * HIST_LEN, maxlen=HIST_LEN),
}
_smooth = {}
_prev_net = None
_level_accum = 0.0  # For audio level smoothing

def _ema(key, value, alpha=0.2):
    _smooth[key] = value if key not in _smooth else _smooth[key] * (1 - alpha) + value * alpha
    return _smooth[key]

def _net_rates():
    global _prev_net
    now = time.time()
    net = psutil.net_io_counters()
    dn = up = 0.0
    if _prev_net:
        dt = max(1e-3, now - _prev_net[2])
        dn = (net.bytes_recv - _prev_net[0]) / dt / 1e6
        up = (net.bytes_sent - _prev_net[1]) / dt / 1e6
    _prev_net = (net.bytes_recv, net.bytes_sent, now)
    return dn, up

# ── Drawing primitives ────────────────────────────────────────────────────

def _draw_mini_gauge(draw, cx, cy, r, value, vmin, vmax, color, label, unit=""):
    """Mini circular gauge with arc + center value."""
    pct = max(0, min(1, (value - vmin) / max(1e-6, vmax - vmin)))

    # Background track (full circle, subtle)
    bbox = [cx - r, cy - r, cx + r, cy + r]
    draw.arc(bbox, 0, 360, fill=(*COLOR_DIM, 50), width=2)

    # Value arc (270 degrees: -135 to +135)
    arc_total = 270
    start_angle = -135
    end_angle = start_angle + pct * arc_total

    if pct > 0.01:
        draw.arc(bbox, start_angle, end_angle, fill=(*color, 255), width=4)

        # End cap dot
        angle_rad = math.radians(end_angle)
        px = cx + (r - 2) * math.cos(angle_rad)
        py = cy + (r - 2) * math.sin(angle_rad)
        draw.ellipse([px-3, py-3, px+3, py+3], fill=(*color, 255))

    # Center value
    val_str = f"{value:.0f}"
    bbox_v = draw.textbbox((0, 0), val_str, font=F_LARGE)
    tw = bbox_v[2] - bbox_v[0]
    th = bbox_v[3] - bbox_v[1]
    draw.text((cx - tw//2, cy - th//2 - 2), val_str, font=F_LARGE, fill=(*COLOR_TEXT, 255))

    # Unit below
    if unit:
        bbox_u = draw.textbbox((0, 0), unit, font=F_SMALL)
        uw = bbox_u[2] - bbox_u[0]
        draw.text((cx - uw//2, cy + r + 4), unit, font=F_SMALL, fill=(*COLOR_DIM, 180))

    # Label above
    bbox_l = draw.textbbox((0, 0), label, font=F_TINY)
    lw = bbox_l[2] - bbox_l[0]
    draw.text((cx - lw//2, cy - r - 14), label, font=F_TINY, fill=(*COLOR_DIM, 160))

def _draw_mini_graph(draw, x, y, w, h, data, color, vmin=0, vmax=100, style="line"):
    """Mini sparkline graph."""
    if len(data) < 2:
        return

    span = max(1e-6, vmax - vmin)

    if style == "line":
        n = max(8, min(len(data), int(w / 4)))
        samp = list(data)[-n:]
        pts = []
        for i, v in enumerate(samp):
            f = max(0, min(1, (v - vmin) / span))
            pts.append((x + (i / (n - 1)) * w, y + h - f * h))

        if len(pts) > 1:
            # Fill area
            draw.polygon(pts + [(x + w, y + h), (x, y + h)], fill=(*color, 40))
            # Line
            draw.line(pts, fill=(*color, 255), width=2, joint="curve")

    else:  # block style
        cols = max(4, int(w / 5))
        samp = list(data)[-cols:]
        bw = w / cols
        for i, v in enumerate(samp):
            f = max(0, min(1, (v - vmin) / span))
            bh = max(1, f * h)
            bx = x + i * bw
            alpha = int(100 + 155 * (i / cols))
            draw.rectangle([bx, y + h - bh, bx + bw - 1, y + h], fill=(*color, alpha))

def _draw_beat_indicator(draw, x, y, level, active):
    """Audio beat indicator with pulsing circle."""
    r_base = 20
    pulse = 0
    if active:
        pulse = int(8 * level) + 4

    # Outer glow
    if pulse > 0:
        draw.ellipse([x - r_base - pulse, y - r_base - pulse,
                      x + r_base + pulse, y + r_base + pulse],
                     fill=(*COLOR_ACCENT, 60))

    # Main circle
    draw.ellipse([x - r_base, y - r_base, x + r_base, y + r_base],
                 outline=(*COLOR_ACCENT, 255), width=3)

    # Center dot based on level
    cr = int(r_base * 0.6 * level)
    if cr > 0:
        draw.ellipse([x - cr, y - cr, x + cr, y + cr],
                     fill=(*COLOR_ACCENT, 255))

def _draw_side_accents(draw, level, accent_color):
    """Animated side borders that react to audio level."""
    # Left side accent
    intensity = int(80 + 175 * level)
    draw.rectangle([0, 0, 4, HEIGHT], fill=(*accent_color, intensity))
    draw.rectangle([WIDTH - 4, 0, WIDTH, HEIGHT], fill=(*accent_color, intensity))

    # Corner accents
    cw = 12
    ch = 12
    draw.rectangle([0, 0, cw, 4], fill=(*accent_color, 255))
    draw.rectangle([0, 0, 4, ch], fill=(*accent_color, 255))
    draw.rectangle([WIDTH - cw, 0, WIDTH, 4], fill=(*accent_color, 255))
    draw.rectangle([WIDTH - 4, 0, WIDTH, ch], fill=(*accent_color, 255))
    draw.rectangle([0, HEIGHT - ch, 4, HEIGHT], fill=(*accent_color, 255))
    draw.rectangle([0, HEIGHT - 4, cw, HEIGHT], fill=(*accent_color, 255))
    draw.rectangle([WIDTH - cw, HEIGHT - 4, WIDTH, HEIGHT], fill=(*accent_color, 255))
    draw.rectangle([WIDTH - 4, HEIGHT - ch, WIDTH, HEIGHT], fill=(*accent_color, 255))

def _status_color(value, is_temp=False):
    """Get color based on value threshold."""
    if is_temp:
        if value >= 80: return COLOR_CRIT
        if value >= 70: return COLOR_WARN
        return COLOR_GPU
    else:
        if value >= 90: return COLOR_CRIT
        if value >= 75: return COLOR_WARN
        return COLOR_ACCENT

# ── Background gradient ────────────────────────────────────────────────────
def _make_background():
    """Create vertical gradient background."""
    bg = Image.new("RGB", (WIDTH, HEIGHT), BG_BOT)
    px = bg.load()
    for y in range(HEIGHT):
        f = y / HEIGHT
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * f)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * f)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * f)
        for x in range(WIDTH):
            px[x, y] = (r, g, b)

    # Add subtle grid pattern
    draw = ImageDraw.Draw(bg)
    for x in range(0, WIDTH, 40):
        draw.line([(x, 0), (x, HEIGHT)], fill=(*COLOR_DIM, 8))
    for y in range(0, HEIGHT, 40):
        draw.line([(0, y), (WIDTH, y)], fill=(*COLOR_DIM, 8))

    return bg

_BG_CACHE = None

def _get_background():
    global _BG_CACHE
    if _BG_CACHE is None:
        _BG_CACHE = _make_background()
    return _BG_CACHE

# ── Frame builder ─────────────────────────────────────────────────────────

def build_frame(bg: "Image.Image | None" = None,
                audio_level: float = 0.0,
                beat_active: bool = False) -> Image.Image:
    """
    Build a multi-visualizer HUD frame.

    Args:
        bg: Optional background image to composite over
        audio_level: Audio level from 0.0 to 1.0 (for beat indicator)
        beat_active: Whether beat is currently active
    """
    # Get accent color from LED config
    accent = _led_accent_color()

    if bg is not None:
        img = bg.copy().convert("RGBA")
        # Darken for overlay readability
        dark = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 140))
        img.alpha_composite(dark)
    else:
        img = _get_background().convert("RGBA")

    draw = ImageDraw.Draw(img, "RGBA")

    # ── Gather stats ───────────────────────────────────────────────────────
    cpu = _ema("cpu", psutil.cpu_percent(interval=None))
    ram = psutil.virtual_memory().percent
    ram = _ema("ram", ram)
    gpu_pct, gpu_vram, gpu_temp = _gpu_stats()
    gpu_pct = _ema("gpu", gpu_pct)
    dn, up = _net_rates()

    # Update history
    _hist["cpu"].append(cpu)
    _hist["ram"].append(ram)
    _hist["gpu"].append(gpu_pct)
    _hist["net"].append(dn)

    now = datetime.now()
    time_str = now.strftime("%H:%M:%S")
    date_str = now.strftime("%a %d %b").upper()

    # ── Layout ─────────────────────────────────────────────────────────────
    PAD = 24
    TOP_Y = 20

    # ── Top row: Mini gauges ───────────────────────────────────────────────
    gauge_r = 38
    gauge_spacing = WIDTH // 4
    gauge_y = 70

    # CPU gauge
    _draw_mini_gauge(draw, PAD + gauge_spacing * 0 + gauge_r, gauge_y, gauge_r,
                     cpu, 0, 100, COLOR_CPU, "CPU", "%")

    # RAM gauge
    _draw_mini_gauge(draw, PAD + gauge_spacing * 1 + gauge_r, gauge_y, gauge_r,
                     ram, 0, 100, COLOR_RAM, "RAM", "%")

    # GPU gauge
    _draw_mini_gauge(draw, PAD + gauge_spacing * 2 + gauge_r, gauge_y, gauge_r,
                     gpu_pct, 0, 100, COLOR_GPU, "GPU", "%")

    # Network gauge (showing download)
    net_gauge_max = max(10.0, max(_hist["net"]) * 1.2)
    _draw_mini_gauge(draw, PAD + gauge_spacing * 3 + gauge_r, gauge_y, gauge_r,
                     dn, 0, net_gauge_max, COLOR_NET, "NET", "MB/s")

    # ── Middle section: Time + Beat + Info ───────────────────────────────────
    mid_y = gauge_y + gauge_r + 40

    # Big time display (centered)
    time_bbox = draw.textbbox((0, 0), time_str, font=F_HUGE)
    time_w = time_bbox[2] - time_bbox[0]
    time_x = WIDTH // 2 - time_w // 2

    # Time shadow
    draw.text((time_x + 3, mid_y + 3), time_str, font=F_HUGE, fill=(*COLOR_DIM, 100))
    # Time main
    draw.text((time_x, mid_y), time_str, font=F_HUGE, fill=(*COLOR_TEXT, 255))

    # Date below time
    date_bbox = draw.textbbox((0, 0), date_str, font=F_SMALL)
    date_w = date_bbox[2] - date_bbox[0]
    draw.text((WIDTH // 2 - date_w // 2, mid_y + 80), date_str,
              font=F_SMALL, fill=(*COLOR_DIM, 180))

    # Beat indicator (left of time)
    beat_x = time_x - 50
    beat_y = mid_y + 35
    _draw_beat_indicator(draw, beat_x, beat_y, audio_level, beat_active)

    # GPU temp (right of time)
    temp_x = time_x + time_w + 50
    temp_y = mid_y + 35
    temp_color = _status_color(gpu_temp, is_temp=True)
    draw.text((temp_x - 15, temp_y - 12), f"{gpu_temp:.0f}°C", font=F_MEDIUM,
              fill=(*temp_color, 255))

    # ── Bottom row: History graphs ──────────────────────────────────────────
    graph_y = mid_y + 120
    graph_h = 60
    graph_pad = 8

    graph_w = (WIDTH - 2 * PAD - 3 * graph_pad) // 4

    # CPU graph
    gx = PAD
    _draw_mini_graph(draw, gx, graph_y, graph_w, graph_h, _hist["cpu"], COLOR_CPU, 0, 100, "line")
    draw.text((gx + 4, graph_y - 16), "CPU", font=F_TINY, fill=(*COLOR_CPU, 180))

    # RAM graph
    gx += graph_w + graph_pad
    _draw_mini_graph(draw, gx, graph_y, graph_w, graph_h, _hist["ram"], COLOR_RAM, 0, 100, "line")
    draw.text((gx + 4, graph_y - 16), "RAM", font=F_TINY, fill=(*COLOR_RAM, 180))

    # GPU graph
    gx += graph_w + graph_pad
    _draw_mini_graph(draw, gx, graph_y, graph_w, graph_h, _hist["gpu"], COLOR_GPU, 0, 100, "line")
    draw.text((gx + 4, graph_y - 16), "GPU", font=F_TINY, fill=(*COLOR_GPU, 180))

    # Network graph
    gx += graph_w + graph_pad
    net_max = max(5.0, max(_hist["net"]) * 1.1)
    _draw_mini_graph(draw, gx, graph_y, graph_w, graph_h, _hist["net"], COLOR_NET, 0, net_max, "line")
    draw.text((gx + 4, graph_y - 16), "NET", font=F_TINY, fill=(*COLOR_NET, 180))

    # ── Network values (bottom right) ───────────────────────────────────────
    net_text_y = HEIGHT - PAD - 20
    draw.text((WIDTH - PAD - 120, net_text_y), f"↓ {dn:.1f} MB/s",
              font=F_SMALL, fill=(*COLOR_NET, 255))
    draw.text((WIDTH - PAD - 60, net_text_y + 18), f"↑ {up:.1f} MB/s",
              font=F_SMALL, fill=(*COLOR_ACCENT, 180))

    # ── Side accents (reactive to audio) ────────────────────────────────────
    _draw_side_accents(draw, audio_level, accent)

    return img.convert("RGB")


# ── Standalone runner ──────────────────────────────────────────────────────
def _run_on_panel():
    from panel import Panel

    psutil.cpu_percent(interval=None)
    _net_rates()

    panel = Panel()
    print("Mode : multi — Ctrl+C pour quitter")

    try:
        while True:
            t0 = time.time()

            # Simulate audio level (replace with real audio in production)
            import random
            level = random.random() * 0.3

            panel.send_image(build_frame(audio_level=level, beat_active=level > 0.2), fit=False)

            elapsed = time.time() - t0
            sleep_time = max(0, REFRESH_RATE - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\nArrêt")
    finally:
        panel.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        psutil.cpu_percent(interval=None)
        _net_rates()
        time.sleep(0.3)

        # Warm up history
        for _ in range(30):
            build_frame()
            time.sleep(0.03)

        build_frame(audio_level=0.5, beat_active=True).save(sys.argv[2])
        print(f"Aperçu écrit → {sys.argv[2]}")
    else:
        _run_on_panel()
