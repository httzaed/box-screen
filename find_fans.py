"""
Allume 8 LEDs à la fois dans la zone Addressable pour trouver les fans.
Appuie sur Entrée pour avancer, Ctrl+C pour quitter.
"""
import led_wave as _lw
from openrgb.utils import RGBColor

_lw._ensure_server()
dev = _lw._device
dev.set_mode("direct")

# Trouve la zone Addressable
zone = None
for z in dev.zones:
    if "Addressable" in z.name:
        zone = z; break

total = len(dev.leds)
zone_offset = sum(len(dev.zones[i].leds) for i in range(dev.zones.index(zone)))
zone_size   = len(zone.leds)

print(f"Device: {dev.name}  total={total}  zone={zone.name}  zone_offset={zone_offset}  zone_size={zone_size}")
print("Les LEDs s'allument par blocs de 8. Dis-moi quel bloc allume les FANS.")

BLACK = [RGBColor(0,0,0)] * total

try:
    for start in range(0, zone_size, 8):
        colors = list(BLACK)
        end = min(start + 8, zone_size)
        for i in range(start, end):
            colors[zone_offset + i] = RGBColor(255, 0, 0)
        dev.set_colors(colors)
        input(f"  zone LED {start:3d}-{end-1:3d}  (total idx {zone_offset+start}-{zone_offset+end-1})  → Entrée pour suivant")
finally:
    dev.set_colors(BLACK)
    print("Noir")
