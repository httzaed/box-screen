"""
render_audio_viz.py — Audio Visualizer
v2 avec mode ambience, spectrum, waveform
"""

import math
import time
import numpy as np
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1920, 462
BG = (13, 10, 25)

# ── Visualization Mode ───────────────────────────────────────────────────────────
_VIZ_MODE = "spectrum"  # défaut panel : spectrum — ambience réservé au dashboard web

def set_viz_mode(mode: str):
    """Set the visualization mode."""
    global _VIZ_MODE
    if mode in ("spectrum", "ambience", "waveform"):
        _VIZ_MODE = mode

def get_viz_mode() -> str:
    """Get the current visualization mode."""
    return _VIZ_MODE


# ── Font Loading ─────────────────────────────────────────────────────────────
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


def _draw_ambience(draw, img, level: float, bass: float, beat_pulse: float, y_offset: int):
    """
    Dessine une visualisation ambience style "flower" avec 4 couches de blobs rotatifs.
    """
    G = (43, 255, 136)
    cx, cy = WIDTH // 2, (HEIGHT - y_offset) // 2 + y_offset

    # Beat impulsion décroissante (1→0 sur 150ms)
    now = time.monotonic()
    try:
        import bpm_source
        last_beat_ts, _ = bpm_source.get_beat_event()
        beat = max(0, 1 - (now - last_beat_ts) / 0.15)
    except:
        beat = beat_pulse

    # Background gradient
    max_dim = max(WIDTH, HEIGHT - y_offset)

    # Version sans beat (fond sombre)
    bg_color = (4, 25, 14)
    if beat > 0.01:
        bg_beat = Image.new("RGBA", (WIDTH, HEIGHT), (*bg_color, int(beat * 30)))
        img.paste(bg_beat, (0, 0), bg_beat)

    # Grid lines défilantes
    gs = 34
    gx = (time.time() * 9) % gs
    grid_alpha = int(0.08 * 255)
    for x in np.arange(-gx, WIDTH, gs):
        draw.line([(x, 0), (x, HEIGHT)], fill=(*G, grid_alpha), width=1)
    for y in np.arange(0, HEIGHT, gs):
        draw.line([(0, y), (WIDTH, y)], fill=(*G, grid_alpha), width=1)

    # Ambience flower - 4 rotating layers
    energy = min(1, level * 1.4)
    base_r = min(WIDTH, HEIGHT - y_offset) * (0.19 + 0.05 * energy + 0.03 * beat)

    # Récupérer les bands de spectre pour les niveaux
    try:
        import bpm_source
        spec_bands = bpm_source.get_viz_spectrum()
        if len(spec_bands) >= 56:
            bands_56 = spec_bands[:56]
        else:
            bands_56 = np.interp(np.linspace(0, len(spec_bands) - 1, 56),
                                np.arange(len(spec_bands)), spec_bands).tolist()

        def band_avg(a, b):
            return sum(bands_56[a:b]) / (b - a) if b > a else 0

        b1 = min(1.2, band_avg(0, 8) * 1.9)
        b2 = min(1.2, band_avg(10, 20) * 1.9)
        b3 = min(1.2, band_avg(24, 38) * 2.2)
        b4 = min(1.2, band_avg(42, 56) * 2.6)
    except:
        b1 = b2 = b3 = b4 = 0.5

    # Pré-calcul des angles
    STEPS = 120
    angles = np.linspace(0, 2 * np.pi, STEPS + 1)
    cos_angles = np.cos(angles)
    sin_angles = np.sin(angles)

    t = time.time()
    layers = 4

    # Dessiner les 4 couches
    for L in range(layers - 1, -1, -1):
        lf = 1 - L * 0.17
        rot_speed = 0.18 + L * 0.07
        rot_dir = 1 if L % 2 == 0 else -1
        rot = t * rot_speed * rot_dir

        # Calculer les rayons
        r = base_r * (
            1 +
            0.60 * b1 * np.sin(angles * 3 + t * 1.3) +
            0.45 * b2 * np.sin(angles * 5 - t * 0.9) +
            0.38 * b3 * np.sin(angles * 8 + t * 0.5) +
            0.30 * b4 * np.sin(angles * 13 - t * 0.7) +
            0.06 * np.sin(angles + t)
        )

        # Clamp rayon
        max_r = min(WIDTH, HEIGHT - y_offset) * 0.48
        r = np.clip(r, 0, max_r)

        # Rotation
        cos_rot = np.cos(rot)
        sin_rot = np.sin(rot)

        x_rot = cos_rot * cos_angles - sin_rot * sin_angles
        y_rot = sin_rot * cos_angles + cos_rot * sin_angles

        x = cx + x_rot * r * lf
        y = cy + y_rot * r * lf

        points = list(zip(x.astype(int), y.astype(int)))

        fade = 1 - L / layers

        if L == 0:
            fill_color = (*G, int((0.05 + beat * 0.10) * 255))
            draw.polygon(points, fill=fill_color)

        stroke_alpha = int(min(1, 0.18 + 0.62 * fade + 0.25 * energy) * 255)
        stroke_width = max(1, int(2.4 - L * 0.35))
        draw.line(points, fill=(*G, stroke_alpha), width=stroke_width)

    # Pulsing core
    cr = base_r * (0.40 + 0.28 * b1 + 0.30 * beat)
    core_radius = int(cr)
    if core_radius > 0:
        steps = 10
        for i in range(steps):
            r_step = core_radius * (1 - i / steps)
            alpha_step = int(255 * (1 - i / steps) * 0.4)
            if alpha_step > 0:
                draw.ellipse([cx - r_step, cy - r_step, cx + r_step, cy + r_step],
                           fill=(*G, alpha_step))

        if beat > 0.01:
            beat_alpha = int(beat * 255)
            for i in range(steps):
                r_step = core_radius * (1 - i / steps)
                alpha_step = int(beat_alpha * (1 - i / steps) * 0.5)
                if alpha_step > 0:
                    draw.ellipse([cx - r_step, cy - r_step, cx + r_step, cy + r_step],
                               fill=(234, 255, 243, alpha_step))


def _draw_spectrum(draw, img, spec, level, bass, beat_active, bpm):
    """Mode spectrum original - barres miroir avec glow"""
    start_x = 50
    total_width = WIDTH - 100
    bar_count = 96
    bar_width = (total_width / bar_count) - 2
    max_bar_height = HEIGHT * 0.24
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

    # ── Timestamp (top right) ────────────────────────────────────────────────────
    now_str = datetime.now().strftime("%H:%M:%S")
    bbox = draw.textbbox((0, 0), now_str, font=F_TIME)
    w = bbox[2] - bbox[0]
    draw.text((WIDTH - 50 - w, 45), now_str, font=F_TIME, fill=(255, 255, 255, 100))


def _draw_waveform(draw, img, level, bass, beat_active):
    """Mode waveform - forme d'onde simple"""
    try:
        import bpm_source
        raw_wave = bpm_source.get_viz_waveform()
        if raw_wave is None:
            raw_wave = [0] * 128
    except:
        raw_wave = [0] * 128

    center_y = HEIGHT // 2
    max_h = HEIGHT * 0.3

    # Draw waveform
    points = []
    n = len(raw_wave)
    for i, val in enumerate(raw_wave):
        x = 50 + (i / (n - 1)) * (WIDTH - 100)
        y = center_y - (val - 0.5) * max_h
        points.append((x, y))

    if len(points) >= 2:
        # Glow
        for offset in [2, 4, 6]:
            glow_points = [(x + offset if i % 2 else x - offset, y) for i, (x, y) in enumerate(points)]
            draw.line(glow_points, fill=(43, 255, 136, 30), width=2, joint='curve')

        # Main line
        draw.line(points, fill=(43, 255, 136, 200), width=2, joint='curve')

    # Center line
    draw.line([(50, center_y), (WIDTH - 50, center_y)], fill=(255, 255, 255, 50), width=1)


def build_frame(bg=None, mode=None, y_offset=0):
    """
    Build audio visualization frame.

    Args:
        bg: Optional background image
        mode: "spectrum" (default), "ambience", or "waveform"
        y_offset: Vertical offset for top bar (cascade mode)
    """
    viz_mode = mode or _VIZ_MODE

    try:
        import bpm_source
        raw_spec = bpm_source.get_viz_spectrum()
        bass, mid, treble = bpm_source.get_viz_bands()
        level = bpm_source.get_level()
        beat_ts, _ = bpm_source.get_beat_event()
        beat_active = (time.monotonic() - beat_ts) < 0.12
        bpm = bpm_source.get_bpm()
    except:
        raw_spec = [0] * 96
        level = 0.5
        beat_active = False
        bpm = None
        bass = mid = treble = 0.3

    # Process spectrum for all modes
    spec = [0] * 96
    n_raw = len(raw_spec)
    start_idx = max(0, n_raw // 4)

    for i in range(96):
        raw_idx = start_idx + int(i * (n_raw - start_idx) / 96)
        if raw_idx < n_raw:
            val = raw_spec[raw_idx]
            boost = 1 + (i / 96) * 0.5
            spec[i] = min(1.0, val * boost)

    for i in range(1, 95):
        spec[i] = (spec[i-1] + spec[i] + spec[i+1]) / 3

    # Background
    if bg:
        img = bg.copy().convert("RGBA")
        if beat_active:
            flash_alpha = int(0.4 * 40)
            flash = Image.new("RGBA", (WIDTH, HEIGHT), (*_get_color(0.5), flash_alpha))
            img.paste(flash, (0, 0), flash)
        overlay = Image.new("RGBA", (WIDTH, HEIGHT), (*BG, 180))
        img.paste(overlay, (0, 0), overlay)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG).convert("RGBA")

    draw = ImageDraw.Draw(img, "RGBA")

    # Mode switching
    if viz_mode == "ambience":
        _draw_ambience(draw, img, level, bass, 0.4 if beat_active else 0, y_offset)
        return img.convert("RGB")

    if viz_mode == "waveform":
        _draw_waveform(draw, img, level, bass, beat_active)
        return img.convert("RGB")

    # Spectrum mode (default)
    _draw_spectrum(draw, img, spec, level, bass, beat_active, bpm)
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
