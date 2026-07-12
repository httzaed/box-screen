"""
LED wave module — importable par launcher.py (box-screen)
Gère le serveur OpenRGB en singleton ; run() tourne dans un thread daemon.
"""
import subprocess
import threading
import time
import math

SPEED = 0.0345   # s entre frames (~29 fps, -15% vitesse)
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


def _find_openrgb() -> str | None:
    """Trouve l'exécutable OpenRGB sur Windows ou Linux."""
    import shutil, os
    # Cherche dans PATH d'abord
    found = shutil.which("openrgb") or shutil.which("OpenRGB")
    if found:
        return found
    # Chemins Windows courants
    candidates = [
        r"C:\Program Files\OpenRGB\OpenRGB.exe",
        r"C:\Program Files (x86)\OpenRGB\OpenRGB.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\OpenRGB\OpenRGB.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _ensure_server() -> bool:
    global _server, _device
    with _lock:
        if _device is not None:
            return True
        try:
            if _is_openrgb_running():
                print("[LED] OpenRGB déjà en cours — connexion directe")
            else:
                openrgb_exe = _find_openrgb()
                if openrgb_exe is None:
                    print("[LED] OpenRGB introuvable — LEDs désactivées")
                    return False
                print("[LED] Démarrage OpenRGB...")
                _server = subprocess.Popen(
                    [openrgb_exe, "--server", "--noautoconnect"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Attendre plus longtemps pour qu'OpenRGB démarre correctement
                for wait_time in [1.0, 0.5, 0.5, 0.5]:  # Total 2.5 secondes
                    time.sleep(wait_time)
                    if _is_openrgb_running():
                        break
                print("[LED] OpenRGB démarré")

            print("[LED] Connexion au client OpenRGB...")
            from openrgb import OpenRGBClient
            client = OpenRGBClient()   # connexion dans le constructeur
            print(f"[LED] {len(client.devices)} devices détectés")

            for d in client.devices:
                if "TUF" in d.name or "B550" in d.name or "Aura" in d.name:
                    _device = d
                    break
            if _device is None:
                _device = client.devices[0]
            # set_mode : essaie "direct" puis "Direct" (sensibilité à la casse selon version)
            try:
                _device.set_mode("direct")
                print("[LED] Mode 'direct' appliqué")
            except Exception:
                try:
                    _device.set_mode("Direct")
                    print("[LED] Mode 'Direct' appliqué")
                except Exception as e:
                    print(f"[LED] Impossible de définir le mode direct: {e}")

            print(f"[LED] {len(_device.leds)} LEDs sur '{_device.name}' — mode direct OK")
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
