"""
render_audio_viz.py — v0 design exact replica
"""

import math
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1920, 462
BG = (13, 10, 25)

def _font(size, bold=False):
    for path in [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except:
            pass
    return ImageFont.load_default()

F_BPM = _font(72, bold=True)
F_BPM_LABEL = _font(20)
F_TIME = _font(16)

def _get_color(t):
    r = int(140 + (255 - 140) * t)
    g = int(82 + (145 - 82) * math.pow(t, 0.5))
    b = int(255 * (1 - t * 0.7) + 77 * t)
    return (r, g, b)


def build_frame(bg=None):
    try:
        import bpm_source
        raw_spec = bpm_source.get_viz_spectrum()
        bass, mid, treble = bpm_source.get_viz_bands()
        level = bpm_source.get_level()
        beat_ts, _ = bpm_source.get_beat_event()
        beat_active = (time.time() - beat_ts) < 0.12
        bpm = bpm_source.get_bpm()
    except:
        raw_spec = [0]*64
        level = 0.5
        beat_active = False
        bpm = None
        bass = mid = treble = 0.3

    # Use only the active part of spectrum (skip static bass region)
    # Start from index 8 (skip first static bars) to get dynamic frequencies
    spec = [0] * 96
    n_raw = len(raw_spec)
    start_idx = max(0, n_raw // 4)  # Skip first quarter (static bass)

    for i in range(96):
        # Map to the active portion of raw spectrum
        raw_idx = start_idx + int(i * (n_raw - start_idx) / 96)
        if raw_idx < n_raw:
            val = raw_spec[raw_idx]
            # Boost high frequencies
            boost = 1 + (i / 96) * 0.5
            spec[i] = min(1.0, val * boost)

    # Apply smoothing
    for i in range(1, 95):
        spec[i] = (spec[i-1] + spec[i] + spec[i+1]) / 3

    # Background
    if bg:
        img = bg.copy().convert("RGBA")
        overlay = Image.new("RGBA", (WIDTH, HEIGHT), (*BG, 180))
        img.alpha_composite(overlay)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG).convert("RGBA")

    draw = ImageDraw.Draw(img, "RGBA")

    # Layout
    start_x = 50
    total_width = WIDTH - 100
    bar_count = 96
    bar_width = (total_width / bar_count) - 2
    max_bar_height = HEIGHT * 0.24  # Plus réduit
    center_y = HEIGHT // 2
    end_x = WIDTH - 50

    # ── Glow Layer ────────────────────────────────────────────────────────────
    for i in range(bar_count):
        val = spec[i]
        if val < 0.05:
            continue

        h = val * max_bar_height
        x = start_x + i * (bar_width + 2)
        t = i / bar_count
        color = _get_color(t)

        glow_width = bar_width + 5
        glow_x = x - 2
        draw.rectangle([glow_x, center_y - h, glow_x + glow_width, center_y],
                       fill=(*color, 35))
        draw.rectangle([glow_x, center_y, glow_x + glow_width, center_y + h],
                       fill=(*color, 35))

    # ── Solid Bars ────────────────────────────────────────────────────────────
    for i in range(bar_count):
        val = spec[i]
        if val < 0.05:
            continue

        h = val * max_bar_height
        x = start_x + i * (bar_width + 2)
        t = i / bar_count
        color = _get_color(t)

        draw.rectangle([x, center_y - h, x + bar_width, center_y],
                       fill=(*color, 255))
        draw.rectangle([x, center_y, x + bar_width, center_y + h],
                       fill=(*color, 255))

    # ── Center line ────────────────────────────────────────────────────────────
    draw.line([(start_x, center_y), (end_x, center_y)],
               fill=(255, 255, 255, 25), width=1)

    # ── Side Bars ────────────────────────────────────────────────────────────
    bar_w = 6
    max_side_h = HEIGHT * 0.3

    beat_boost = 0.25 if beat_active else 0
    side_h = (bass * 0.6 + level * 0.4 + beat_boost) * max_side_h
    side_h = min(side_h, max_side_h)

    left_y = center_y - side_h // 2
    right_x = WIDTH - 24
    right_y = center_y - side_h // 2

    # Left bar
    for g in range(2):
        gw = bar_w + (2-g) * 5
        gx = 18 - (gw - bar_w) // 2
        draw.rectangle([gx, left_y, gx + gw, left_y + side_h],
                       fill=(140, 82, 255, 15 + g * 12))
    draw.rectangle([18, left_y, 18 + bar_w, left_y + side_h],
                   fill=(140, 82, 255, 255))

    # Right bar
    for g in range(2):
        gw = bar_w + (2-g) * 5
        gx = right_x - (gw - bar_w) // 2
        draw.rectangle([gx, right_y, gx + gw, right_y + side_h],
                       fill=(255, 145, 77, 15 + g * 12))
    draw.rectangle([right_x, right_y, right_x + bar_w, right_y + side_h],
                   fill=(255, 145, 77, 255))

    # ── BPM (top left) ────────────────────────────────────────────────────────
    if bpm:
        glow_alpha = 90 + (50 if beat_active else 0)
        for g in range(2):
            draw.text((50 + g*2, 35 + g*2), f"{int(bpm)}",
                      font=F_BPM, fill=(140, 82, 255, glow_alpha - g*30))

        draw.text((50, 35), f"{int(bpm)}", font=F_BPM, fill=(255, 255, 255, 255))
        draw.text((165, 65), "BPM", font=F_BPM_LABEL, fill=(180, 160, 255, 180))

    # ── Timestamp (top right) ───────────────────────────────────────────────────
    now = datetime.now().strftime("%H:%M:%S")
    bbox = draw.textbbox((0, 0), now, font=F_TIME)
    w = bbox[2] - bbox[0]
    draw.text((WIDTH - 50 - w, 45), now, font=F_TIME, fill=(255, 255, 255, 100))

    return img.convert("RGB")


def _run_on_panel():
    from panel import Panel
    panel = Panel()
    try:
        while True:
            panel.send_image(build_frame(), fit=False)
            time.sleep(0.033)
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        panel.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        build_frame().save(sys.argv[2])
    else:
        _run_on_panel()
