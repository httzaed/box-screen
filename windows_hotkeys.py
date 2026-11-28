"""Global Windows shortcuts registered without a low-level keyboard hook."""
import ctypes
from ctypes import wintypes
import threading


def register(bindings):
    ready = threading.Event()
    registered = []

    def listen():
        user32 = ctypes.windll.user32
        try:
            for index, (modifiers, key, callback) in enumerate(bindings):
                if user32.RegisterHotKey(None, index, modifiers | 0x4000, key):
                    registered.append(index)
                else:
                    print(f"[Hotkeys] Raccourci indisponible (mod={modifiers}, touche={key})")
            ready.set()
            msg = wintypes.MSG()
            while registered and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312 and msg.wParam in registered:
                    threading.Thread(target=bindings[msg.wParam][2], daemon=True).start()
        finally:
            ready.set()
            for index in registered:
                user32.UnregisterHotKey(None, index)

    if not hasattr(ctypes, "windll"):
        return False
    threading.Thread(target=listen, name="windows-hotkeys", daemon=True).start()
    ready.wait()
    return len(registered) == len(bindings)
