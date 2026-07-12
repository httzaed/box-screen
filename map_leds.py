"""
map_leds.py — Carte interactive des LEDs OpenRGB
Allume 1 LED à la fois et tu la tagges : case / fan1..8 / mb / skip

Résultat sauvegardé dans led_map.json
"""
import json
import sys
import led_wave as _lw
from openrgb.utils import RGBColor

if not _lw._ensure_server():
    print("\n[ERREUR] OpenRGB n'est pas accessible. Assurez-vous que:")
    print("  1. OpenRGB est lance")
    print("  2. Le SDK OpenRGB Python est installe: pip install openrgb-sdk")
    sys.exit(1)

dev = _lw._device
try:
    dev.set_mode("direct")
except Exception as e:
    print(f"\n[ERREUR] Impossible de definir le mode 'direct': {e}")
    sys.exit(1)

total = len(dev.leds)
print(f"\nDevice : {dev.name}  —  {total} LEDs totales")
for i, z in enumerate(dev.zones):
    print(f"  Zone {i}: '{z.name}'  {len(z.leds)} LEDs")

# Choisir la zone à mapper
zone_idx = int(input("\nNuméro de zone à mapper (généralement 1 pour Addressable) : ").strip())
zone        = dev.zones[zone_idx]
zone_offset = sum(len(dev.zones[i].leds) for i in range(zone_idx))
zone_size   = len(zone.leds)
print(f"\nZone '{zone.name}'  —  {zone_size} LEDs  (offset global {zone_offset})\n")

TAGS = {
    "c": "case",
    "f": "fans",   # tous les fans en parallèle sur ce canal
    "m": "mainboard",
    "s": "skip",
}

print("Touches :")
print("  c        → case")
print("  f        → fans  (tous en parallèle)")
print("  m        → mainboard")
print("  s        → skip")
print("  u        → undo")
print("  Entrée   → répète le tag précédent")
print("  q        → quitter et sauvegarder\n")

BLACK  = [RGBColor(0, 0, 0)] * total
mapping = {}   # {led_idx_in_zone: tag}

# Charge un mapping existant si présent
try:
    with open("led_map.json") as f:
        saved = json.load(f)
    mapping = {int(k): v for k, v in saved.get("mapping", {}).items()}
    print(f"Mapping existant chargé ({len(mapping)} LEDs déjà taggées)")
except FileNotFoundError:
    pass

last_tag = "case"
i = 0
while i < zone_size:
    # Allume la LED courante
    colors = list(BLACK)
    colors[zone_offset + i] = RGBColor(0, 255, 80)   # vert vif
    dev.set_colors(colors)

    existing = f" [{mapping[i]}]" if i in mapping else ""
    try:
        raw = input(f"  LED {i:3d}{existing} > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        break

    if raw == "q":
        break
    if raw == "u":
        if i > 0:
            i -= 1
            if i in mapping:
                del mapping[i]
        continue
    if raw == "":
        raw = {"case": "c", "fans": "f", "mainboard": "m", "skip": "s"}.get(last_tag, "c")

    tag = TAGS.get(raw)
    if tag is None:
        print(f"    Touche inconnue : {raw!r}  (c/m/1-8/s/u/q)")
        continue

    mapping[i] = tag
    last_tag   = tag
    i += 1

# Éteint tout
dev.set_colors(BLACK)

# Sauvegarde
groups: dict[str, list[int]] = {}
for led_i, tag in mapping.items():
    groups.setdefault(tag, []).append(led_i)

result = {
    "device":      dev.name,
    "zone_idx":    zone_idx,
    "zone_offset": zone_offset,
    "zone_size":   zone_size,
    "mapping":     {str(k): v for k, v in mapping.items()},
    "groups":      {tag: sorted(idxs) for tag, idxs in groups.items()},
}

with open("led_map.json", "w") as f:
    json.dump(result, f, indent=2)

print(f"\nSauvegardé dans led_map.json")
print("Résumé :")
for tag, idxs in sorted(groups.items()):
    # Compresse en ranges pour lisibilité
    ranges = []
    start = prev = idxs[0]
    for n in idxs[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    print(f"  {tag:12s} : {len(idxs):3d} LEDs  [{', '.join(ranges)}]")
