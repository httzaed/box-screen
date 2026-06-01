"""
LED wave module — importable par launcher.py (box-screen)
Gère le serveur OpenRGB en singleton ; run() tourne dans un thread daemon.
"""
import subprocess
import threading
import time
import math

SPEED = 0.03   # s entre frames (~33 fps)
BAND  = 18     # largeur d'une bande en LEDs

# ── Singleton OpenRGB ────────────────────────────────────────────────────────
_server = None
_device = None
_lock   = threading.Lock()


def _is_openrgb_running() -> bool:
    """Vérifie si un serveur OpenRGB écoute déjà sur le port 6742."""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 6742), timeout=0.5)
        s.close()
        return True
    except OSError:
        return False


def _ensure_server() -> bool:
    global _server, _device
    with _lock:
        if _device is not None:
            return True
        try:
            if _is_openrgb_running():
                print("[LED] OpenRGB déjà en cours — connexion directe")
            else:
                _server = subprocess.Popen(
                    ["/usr/bin/openrgb", "--server", "--noautoconnect"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(1.5)
            from openrgb import OpenRGBClient
            client = OpenRGBClient()
            client.connect()
            for d in client.devices:
                if "TUF" in d.name or "B550" in d.name or "Aura" in d.name:
                    _device = d
                    break
            if _device is None:
                _device = client.devices[0]
            _device.set_mode("direct")
            print(f"[LED] {len(_device.leds)} LEDs sur '{_device.name}'")
            return True
        except Exception as e:
            print(f"[LED] init error: {e}")
            return False


def run(stop_event: threading.Event, get_colors):
    """
    Anime les LEDs en vague jusqu'à ce que stop_event soit positionné.
    get_colors() doit retourner (c1, c2) — tuples RGB (r, g, b) 0-255.
    """
    if not _ensure_server():
        return
    from openrgb.utils import RGBColor
    num_leds = len(_device.leds)
    half     = BAND
    cycle    = BAND * 2
    offset   = 0
    while not stop_event.is_set():
        c1, c2 = get_colors()
        colors = []
        for i in range(num_leds):
            pos    = (i + offset) % cycle
            color  = c1 if pos < half else c2
            t      = (pos % half) / half
            bright = 0.5 + 0.5 * math.sin(t * math.pi)
            colors.append(RGBColor(
                int(color[0] * bright),
                int(color[1] * bright),
                int(color[2] * bright),
            ))
        _device.set_colors(colors)
        offset += 1
        time.sleep(SPEED)


def shutdown():
    global _server, _device
    _device = None
    if _server:
        _server.terminate()
        _server = None
