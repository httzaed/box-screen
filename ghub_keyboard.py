"""Clavier Logitech piloté par le SDK lumière de G Hub.

Quand G Hub est ouvert, il garde le clavier pour lui et OpenRGB ne le voit plus.
Ce module ne se bat pas contre G Hub : il lui envoie nos couleurs, et G Hub les
applique au clavier. À la fermeture, G Hub reprend son propre éclairage.

Réglage à vérifier dans G Hub : Paramètres > « Autoriser les jeux et les
applications à contrôler l'éclairage » doit être activé.

La bibliothèque est celle installée par G Hub (chemin lu dans le registre).
En secours : LogitechLedEnginesWrapper.dll dans ce dossier, ou le paquet pip logiled.
"""

import atexit
import ctypes
import os
import time
from pathlib import Path

_CLSID = r"SOFTWARE\Classes\CLSID\{a6519e67-7632-4375-afdf-caa889744403}\ServerBinary"
_PERKEY_RGB = 4           # LOGI_DEVICETYPE_PERKEY_RGB : claviers RGB touche par touche
_BMP_W, _BMP_H = 21, 6    # grille du SDK, 4 octets par touche (BGRA)
_MAX_FPS = 30

_dll = None
_dll_path = None


def _candidates():
    paths = []
    if os.name != "nt":
        return paths
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, 0):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CLSID, 0, winreg.KEY_READ | view) as key:
                value, _ = winreg.QueryValueEx(key, "")
                paths.append(os.path.expandvars(str(value)))
        except OSError:
            pass
    paths.append(str(Path(__file__).resolve().parent / "LogitechLedEnginesWrapper.dll"))
    try:
        import importlib.util
        spec = importlib.util.find_spec("logiled")
        if spec and spec.origin:
            paths.append(str(Path(spec.origin).parent / "dll" / "LogitechLedEnginesWrapper.dll"))
    except Exception:
        pass
    return list(dict.fromkeys(p for p in paths if p))


def _load():
    global _dll, _dll_path
    if _dll is not None:
        return _dll
    errors = []
    for path in _candidates():
        if not os.path.isfile(path):
            continue
        try:
            dll = ctypes.CDLL(path)
            dll.LogiLedInit  # vérifie que c'est bien le SDK lumière
        except (OSError, AttributeError) as error:
            errors.append(f"{Path(path).name}: {error}")
            continue
        dll.LogiLedInit.restype = ctypes.c_bool
        if hasattr(dll, "LogiLedInitWithName"):
            dll.LogiLedInitWithName.argtypes = [ctypes.c_char_p]
            dll.LogiLedInitWithName.restype = ctypes.c_bool
        dll.LogiLedSetTargetDevice.argtypes = [ctypes.c_int]
        dll.LogiLedSetTargetDevice.restype = ctypes.c_bool
        dll.LogiLedSetLighting.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll.LogiLedSetLighting.restype = ctypes.c_bool
        dll.LogiLedSetLightingFromBitmap.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
        dll.LogiLedSetLightingFromBitmap.restype = ctypes.c_bool
        dll.LogiLedShutdown.restype = None
        _dll, _dll_path = dll, path
        return dll
    detail = f" ({'; '.join(errors)})" if errors else ""
    raise RuntimeError("SDK lumière de G Hub introuvable : G Hub est-il installé ?" + detail)


def _percent(value):
    return max(0, min(100, round(value * 100 / 255)))


class GHubKeyboard:
    """Même interface que le clavier OpenRGB utilisé par led_fans : .name, .leds, set_colors()."""

    def __init__(self, n_leds=94, name="G915 via G Hub"):
        self._dll = _load()
        init = getattr(self._dll, "LogiLedInitWithName", None)
        ok = init(b"Box Screen") if init else self._dll.LogiLedInit()
        if not ok:
            raise RuntimeError("G Hub ne répond pas (ouvert ? contrôle de l'éclairage par les applications activé ?)")
        self._closed = False
        time.sleep(0.3)  # le SDK a besoin d'un court délai après l'init
        self._dll.LogiLedSetTargetDevice(_PERKEY_RGB)
        self.name = name
        self.path = _dll_path
        self.leds = [None] * max(1, int(n_leds))
        self._bitmap = (ctypes.c_ubyte * (_BMP_W * _BMP_H * 4))()
        self._last = None
        self._next = 0.0
        atexit.register(self.close)

    def set_colors(self, colors, fast=True):
        if self._closed:
            raise RuntimeError("SDK G Hub fermé")
        rgb = tuple((c.red, c.green, c.blue) for c in colors)
        now = time.monotonic()
        if not rgb or rgb == self._last or now < self._next:
            return
        if len(set(rgb)) == 1:
            r, g, b = rgb[0]
            ok = self._dll.LogiLedSetLighting(_percent(r), _percent(g), _percent(b))
        else:
            # Les couleurs suivent l'ordre des LEDs : on les étale de gauche à droite.
            n, bm = len(rgb), self._bitmap
            for col in range(_BMP_W):
                r, g, b = rgb[min(n - 1, col * n // _BMP_W)]
                for row in range(_BMP_H):
                    i = (row * _BMP_W + col) * 4
                    bm[i], bm[i + 1], bm[i + 2], bm[i + 3] = b, g, r, 255
            ok = self._dll.LogiLedSetLightingFromBitmap(bm)
        if not ok:
            raise RuntimeError("G Hub a refusé les couleurs (fermé entre-temps ?)")
        self._last = rgb
        self._next = now + 1 / _MAX_FPS

    def close(self):
        if not getattr(self, "_closed", True):
            self._closed = True
            try:
                self._dll.LogiLedShutdown()  # G Hub reprend son propre éclairage
            except Exception:
                pass
