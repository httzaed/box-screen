#!/usr/bin/env python3
"""
ST wave animation — violet/rose + orange-rouge vif
Utilise le SDK OpenRGB pour une animation fluide sans flash
"""
import time
import math
import sys
import signal

C1 = (0xDD, 0x00, 0xFF)  # violet/rose vif
C2 = (0xFF, 0x28, 0x00)  # orange-rouge pétant

SPEED = 0.0345   # secondes entre frames (~29 fps, -15% vitesse)
BAND  = 18     # largeur d'une bande en LEDs

# ── se connecter au serveur OpenRGB déjà lancé (via OpenRGB.desktop autostart) ─
# On attend qu'il soit dispo (jusqu'à 10s)
import socket

def wait_for_server(host="localhost", port=6742, timeout=10):
    for _ in range(timeout * 10):
        try:
            s = socket.create_connection((host, port), timeout=0.1)
            s.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False

if not wait_for_server():
    print("OpenRGB server not available after 10s, aborting.")
    sys.exit(1)

try:
    from openrgb import OpenRGBClient
    from openrgb.utils import RGBColor

    client = OpenRGBClient()
    client.connect()

    # trouver le bon device
    device = None
    for d in client.devices:
        if "TUF" in d.name or "B550" in d.name or "Aura" in d.name:
            device = d
            break
    if device is None:
        device = client.devices[0]

    device.set_mode("direct")
    num_leds = len(device.leds)
    print(f"Animating {num_leds} LEDs on '{device.name}' — Ctrl+C to stop.")

    def wave_color(led_index, offset):
        cycle = BAND * 2
        pos = (led_index + offset) % cycle
        half = BAND

        color = C1 if pos < half else C2

        # brightness: sine avec plancher à 0.5 → jamais de flash noir
        t = (pos % half) / half
        brightness = 0.5 + 0.5 * math.sin(t * math.pi)

        return RGBColor(
            int(color[0] * brightness),
            int(color[1] * brightness),
            int(color[2] * brightness),
        )

    def stop(sig=None, frame=None):
        print("\nStopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)

    offset = 0
    while True:
        colors = [wave_color(i, offset) for i in range(num_leds)]
        device.set_colors(colors)
        offset += 1
        time.sleep(SPEED)

except Exception as e:
    print(f"SDK error: {e}")
    sys.exit(1)
