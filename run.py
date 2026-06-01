"""
Lance ce fichier. Modifie render.py à chaud — le panel se met à jour.
"""

import importlib
import sys
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import render
from panel import Panel

WATCH_FILE = Path(__file__).parent / "render.py"


class RenderReloader(FileSystemEventHandler):
    def __init__(self):
        self.dirty = False

    def on_modified(self, event):
        if Path(event.src_path).resolve() == WATCH_FILE.resolve():
            self.dirty = True


def reload_render():
    try:
        importlib.reload(render)
        print("render.py rechargé")
        return True
    except Exception as e:
        print(f"Erreur dans render.py : {e}")
        return False


if __name__ == "__main__":
    panel = Panel()

    reloader = RenderReloader()
    observer = Observer()
    observer.schedule(reloader, str(WATCH_FILE.parent), recursive=False)
    observer.start()

    print(f"Watching {WATCH_FILE.name} — modifie-le pour mettre à jour le panel")
    print("Ctrl+C pour quitter\n")

    try:
        # baseline CPU
        render.psutil.cpu_percent(interval=None)

        while True:
            if reloader.dirty:
                reloader.dirty = False
                reload_render()

            t0 = time.time()
            try:
                frame = render.build_frame()
                panel.send_image(frame)
            except Exception as e:
                print(f"Erreur frame : {e}")

            elapsed = time.time() - t0
            time.sleep(max(0, render.REFRESH_RATE - elapsed))

    except KeyboardInterrupt:
        print("\nArrêt")
    finally:
        observer.stop()
        observer.join()
        panel.close()
