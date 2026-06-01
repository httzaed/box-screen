"""
Vidéo en fond + cadrans par dessus.
Usage : python run_video.py "video.mp4"
"""
import sys
import time
import cv2
import importlib
from pathlib import Path
from PIL import Image

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import render
from panel import Panel, WIDTH, HEIGHT

if len(sys.argv) < 2:
    print("Usage : python run_video.py <fichier_video>")
    sys.exit(1)

VIDEO = sys.argv[1]

# ── Hot reload render.py ───────────────────────────────────────────────────
class RenderReloader(FileSystemEventHandler):
    def __init__(self): self.dirty = False
    def on_modified(self, event):
        if Path(event.src_path).resolve() == (Path(__file__).parent / "render.py").resolve():
            self.dirty = True

def _fit_crop(img: Image.Image) -> Image.Image:
    src_ratio = img.width / img.height
    dst_ratio = WIDTH / HEIGHT
    if src_ratio > dst_ratio:
        new_h = HEIGHT; new_w = int(HEIGHT * src_ratio)
    else:
        new_w = WIDTH;  new_h = int(WIDTH / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    x = (new_w - WIDTH) // 2; y = (new_h - HEIGHT) // 2
    return img.crop((x, y, x + WIDTH, y + HEIGHT))

# ── Main ───────────────────────────────────────────────────────────────────
panel   = Panel()
cap     = cv2.VideoCapture(VIDEO)
src_fps = cap.get(cv2.CAP_PROP_FPS) or 25

reloader = RenderReloader()
observer = Observer()
observer.schedule(reloader, str(Path(__file__).parent), recursive=False)
observer.start()

print(f"Vidéo : {VIDEO}")
print("Modifie render.py à chaud — Ctrl+C pour quitter\n")

try:
    render.psutil.cpu_percent(interval=None)

    while True:
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        if reloader.dirty:
            reloader.dirty = False
            try:
                importlib.reload(render)
                print("render.py rechargé")
            except Exception as e:
                print(f"Erreur render.py : {e}")

        t0 = time.time()

        # Fond vidéo croppé
        bg = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        render.background_override = _fit_crop(bg)

        # Cadrans par dessus
        frame_img = render.build_frame()
        panel.send_image(frame_img, fit=False)

        elapsed = time.time() - t0
        time.sleep(max(0, 1 / src_fps - elapsed))

except KeyboardInterrupt:
    print("\nArrêt")
finally:
    observer.stop()
    observer.join()
    cap.release()
    panel.close()
