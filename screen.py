"""
Trofeo Vision 9.16 — overlay stats temps réel
CPU / RAM / GPU — fond personnalisable
"""

import time
import psutil
from PIL import Image, ImageDraw, ImageFont
from panel import Panel, WIDTH, HEIGHT

# ── Config ─────────────────────────────────────────────────────────────────
BACKGROUND   = r"C:\Users\hedik\Downloads\jungle-landscape-pixel-art-style.png"
REFRESH_RATE = 1.0   # secondes entre chaque frame

# Couleurs
COLOR_CPU  = (140, 82, 255)   # violet
COLOR_RAM  = (255, 145,  77)  # orange
COLOR_GPU  = ( 77, 220, 255)  # cyan
COLOR_TEXT = (255, 255, 255)

# ── GPU (optionnel — nécessite pynvml) ─────────────────────────────────────
try:
    import pynvml
    pynvml.nvmlInit()
    _gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_OK = True
except Exception:
    _GPU_OK = False


def _gpu_stats() -> tuple[float, float, int]:
    """Retourne (usage%, vram_used_go, temp_C). (0,0,0) si indisponible."""
    if not _GPU_OK:
        return 0.0, 0.0, 0
    try:
        util  = pynvml.nvmlDeviceGetUtilizationRates(_gpu_handle)
        mem   = pynvml.nvmlDeviceGetMemoryInfo(_gpu_handle)
        temp  = pynvml.nvmlDeviceGetTemperature(_gpu_handle, pynvml.NVML_TEMPERATURE_GPU)
        return util.gpu, mem.used / 1e9, temp
    except Exception:
        return 0.0, 0.0, 0


# ── Font ───────────────────────────────────────────────────────────────────
def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\Montserrat-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()

FONT_BIG   = _load_font(52)
FONT_SMALL = _load_font(28)


# ── Fond ───────────────────────────────────────────────────────────────────
try:
    _bg = Image.open(BACKGROUND).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    print(f"Fond chargé : {BACKGROUND}")
except Exception:
    _bg = Image.new("RGB", (WIDTH, HEIGHT), (10, 0, 20))
    print("Fond par défaut (image non trouvée)")


# ── Build frame ────────────────────────────────────────────────────────────
def build_frame() -> Image.Image:
    img  = _bg.copy()
    draw = ImageDraw.Draw(img, "RGBA")

    cpu  = psutil.cpu_percent(interval=None)
    ram  = psutil.virtual_memory()
    gpu_pct, gpu_vram, gpu_temp = _gpu_stats()

    # Bandeau semi-transparent en bas
    draw.rectangle([(0, HEIGHT - 120), (WIDTH, HEIGHT)], fill=(0, 0, 0, 160))

    # ── CPU ────────────────────────────────────────────────────────────────
    x = 60
    draw.text((x, HEIGHT - 110), "CPU", font=FONT_SMALL, fill=COLOR_CPU)
    draw.text((x, HEIGHT - 78),  f"{cpu:.0f}%", font=FONT_BIG, fill=COLOR_CPU)

    # Barre CPU
    bar_w = 200
    draw.rectangle([(x, HEIGHT - 22), (x + bar_w, HEIGHT - 10)], fill=(60, 30, 100))
    draw.rectangle([(x, HEIGHT - 22), (x + int(bar_w * cpu / 100), HEIGHT - 10)], fill=COLOR_CPU)

    # ── RAM ────────────────────────────────────────────────────────────────
    x = 340
    used_go = ram.used / 1e9
    total_go = ram.total / 1e9
    draw.text((x, HEIGHT - 110), "RAM", font=FONT_SMALL, fill=COLOR_RAM)
    draw.text((x, HEIGHT - 78),  f"{used_go:.1f}/{total_go:.0f}G", font=FONT_BIG, fill=COLOR_RAM)

    bar_w = 240
    draw.rectangle([(x, HEIGHT - 22), (x + bar_w, HEIGHT - 10)], fill=(100, 50, 20))
    draw.rectangle([(x, HEIGHT - 22), (x + int(bar_w * ram.percent / 100), HEIGHT - 10)], fill=COLOR_RAM)

    # ── GPU ────────────────────────────────────────────────────────────────
    if _GPU_OK:
        x = 680
        draw.text((x, HEIGHT - 110), "GPU", font=FONT_SMALL, fill=COLOR_GPU)
        draw.text((x, HEIGHT - 78),  f"{gpu_pct:.0f}%  {gpu_temp}°C", font=FONT_BIG, fill=COLOR_GPU)
        bar_w = 220
        draw.rectangle([(x, HEIGHT - 22), (x + bar_w, HEIGHT - 10)], fill=(20, 60, 80))
        draw.rectangle([(x, HEIGHT - 22), (x + int(bar_w * gpu_pct / 100), HEIGHT - 10)], fill=COLOR_GPU)

    # ── Horloge ────────────────────────────────────────────────────────────
    from datetime import datetime
    now = datetime.now().strftime("%H:%M:%S")
    draw.text((WIDTH - 160, HEIGHT - 90), now, font=FONT_BIG, fill=COLOR_TEXT)

    return img


# ── Loop principal ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    panel = Panel()
    print(f"Loop démarré — refresh {REFRESH_RATE}s  |  Ctrl+C pour quitter")
    try:
        psutil.cpu_percent(interval=None)  # premier appel = baseline
        while True:
            t0 = time.time()
            frame = build_frame()
            panel.send_image(frame)
            elapsed = time.time() - t0
            wait = max(0, REFRESH_RATE - elapsed)
            time.sleep(wait)
    except KeyboardInterrupt:
        print("\nArrêt")
    finally:
        panel.close()
