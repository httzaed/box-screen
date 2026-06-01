"""
Lecture vidéo sur le panel — loop automatique.
Usage : python video.py "C:/chemin/vers/video.mp4"
"""
import sys
import time
import cv2
from PIL import Image
from panel import Panel, WIDTH, HEIGHT

if len(sys.argv) < 2:
    print("Usage : python video.py <fichier_video>")
    sys.exit(1)

VIDEO = sys.argv[1]
FIT     = "--fit"     in sys.argv   # garde le ratio, crop centré
PALETTE = "--palette" in sys.argv   # affiche la palette des couleurs dominantes

panel = Panel()
cap   = cv2.VideoCapture(VIDEO)

if not cap.isOpened():
    print(f"Impossible d'ouvrir : {VIDEO}")
    sys.exit(1)

src_fps    = cap.get(cv2.CAP_PROP_FPS) or 30
src_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print(f"Vidéo : {src_fps:.1f} fps  {src_frames} frames")
print("Ctrl+C pour quitter\n")

_palette_colors = []   # palette courante (lissée)
_palette_frame  = 0    # compteur pour mise à jour
PALETTE_UPDATE  = int(src_fps * 3600)  # recalcul toutes les heures

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        t0  = time.time()
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        # Appliquer fit/crop d'abord
        from panel import _image_to_jpeg, _send_frame
        from panel import WIDTH, HEIGHT
        if FIT:
            src_ratio = img.width / img.height
            dst_ratio = WIDTH / HEIGHT
            if src_ratio > dst_ratio:
                new_h = HEIGHT; new_w = int(HEIGHT * src_ratio)
            else:
                new_w = WIDTH;  new_h = int(WIDTH / src_ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            x = (new_w - WIDTH) // 2; y = (new_h - HEIGHT) // 2
            img = img.crop((x, y, x + WIDTH, y + HEIGHT))
        else:
            img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)

        # Palette sur l'image déjà croppée
        if PALETTE:
            from PIL import ImageDraw

            _palette_frame  += 1
            if _palette_frame % PALETTE_UPDATE == 0 or not _palette_colors:
                small  = img.resize((80, 46), Image.LANCZOS)
                quant  = small.quantize(colors=6, method=Image.Quantize.FASTOCTREE).convert("RGB")
                new_colors = []
                seen = set()
                for c in quant.getdata():
                    key = (c[0]//20, c[1]//20, c[2]//20)
                    if key not in seen:
                        seen.add(key)
                        new_colors.append(c)
                    if len(new_colors) == 6:
                        break
                # Lissage : moyenne pondérée entre ancienne et nouvelle palette
                if _palette_colors and len(_palette_colors) == len(new_colors):
                    alpha = 0.3  # 0=figé, 1=instantané
                    _palette_colors = [
                        tuple(int(_palette_colors[i][j] * (1-alpha) + new_colors[i][j] * alpha) for j in range(3))
                        for i in range(len(new_colors))
                    ]
                else:
                    _palette_colors = new_colors

            # Couleurs texte crème : mélange palette + crème
            CREAM = (230, 220, 200)
            def luminance(c):
                return 0.299*c[0] + 0.587*c[1] + 0.114*c[2]

            import colorsys

            def hsv(c):
                return colorsys.rgb_to_hsv(c[0]/255, c[1]/255, c[2]/255)

            def accent_score(c):
                h, s, v = hsv(c)
                # Boost hues chaudes : rouge (0-0.08 ou 0.92-1.0) et orange (0.05-0.12)
                warm = 1.5 if (h < 0.12 or h > 0.92) else 1.0
                return s * v * warm

            creamy = sorted([
                tuple(int(c[j] * 0.5 + CREAM[j] * 0.5) for j in range(3))
                for c in _palette_colors
            ], key=luminance, reverse=True)
            light_colors = creamy[:3]
            dark_colors  = creamy[-3:]

            # Accent : pixel le plus vif (saturation × luminosité max, indépendant de la fréquence)
            tiny = img.resize((40, 23), Image.LANCZOS).convert("RGB")
            accent = max(tiny.getdata(), key=accent_score)

            import json, pathlib
            pathlib.Path("palette.json").write_text(json.dumps({
                "text":      light_colors,
                "indicator": dark_colors,
                "accent":    list(accent),
            }))

            from PIL import ImageFont as _IF
            try:
                _fnt = _IF.truetype(r"C:\Users\hedik\AppData\Local\Microsoft\Windows\Fonts\Montserrat-SemiBold.ttf", 18)
            except Exception:
                _fnt = _IF.load_default()

            draw   = ImageDraw.Draw(img)
            sw, sh = 52, 52
            margin = 16

            # Palette brute — ligne de swatches en haut à droite
            n   = len(_palette_colors)
            x0  = WIDTH - (sw + 6) * n - margin
            y0  = margin
            for i, col in enumerate(_palette_colors):
                x = x0 + i * (sw + 6)
                draw.rounded_rectangle([(x, y0), (x+sw, y0+sh)], radius=8, fill=col)

            # Accent swatch + label
            ax = margin
            draw.rounded_rectangle([(ax, y0), (ax+sw, y0+sh)], radius=8, fill=accent)
            draw.text((ax + sw + 8, y0 + 16), "ACCENT", font=_fnt, fill=accent)

            # Texte clairs — labels sous la palette
            y1 = y0 + sh + 10
            labels = ["TEXT", "CPU", "RAM"]
            for i, (col, label) in enumerate(zip(light_colors, labels)):
                x = x0 + i * (sw + 6)
                draw.rounded_rectangle([(x, y1), (x+sw//2, y1+14)], radius=4, fill=col)
                draw.text((x + sw//2 + 4, y1), label, font=_fnt, fill=col)

        panel.send_image(img, fit=False)
        elapsed = time.time() - t0

        # Respecte le framerate source (si le panel est assez rapide)
        wait = max(0, 1 / src_fps - elapsed)
        time.sleep(wait)

except KeyboardInterrupt:
    print("\nArrêt")
finally:
    cap.release()
    panel.close()
