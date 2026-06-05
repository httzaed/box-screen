#!/usr/bin/env python
"""reset_panel.py — Reset the USB panel device"""

import usb.core
import time
import sys

VID = 0x0416
PID = 0x5408

print("Recherche du panel...")
dev = usb.core.find(idVendor=VID, idProduct=PID)

if dev is None:
    print("Panel non trouvé - branche le câble USB")
    sys.exit(1)

print(f"Panel trouvé: {dev}")

try:
    print("Reset du device...")
    dev.reset()
    print("Reset OK")
    time.sleep(1)
except Exception as e:
    print(f"Reset error: {e}")

# Verify it's back
print("Vérification...")
dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev:
    print("Panel prêt ✓")
else:
    print("Panel non détecté ✗")
