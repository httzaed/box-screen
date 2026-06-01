"""Mesure le framerate max du panel."""
import time
from PIL import Image
from panel import Panel

panel = Panel()
img = Image.new("RGB", (1920, 462), (20, 0, 40))

print("Benchmark 30 frames...")
t0 = time.time()
for i in range(30):
    panel.send_image(img)
elapsed = time.time() - t0

print(f"Résultat : {30/elapsed:.1f} fps  ({elapsed/30*1000:.0f} ms/frame)")
panel.close()
