"""Start Phosphor and its LED driver without requiring the LCD panel."""
import socket
import threading
import webbrowser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / '.phosphor-deps'))


def main():
    url = 'http://127.0.0.1:7420'
    try:
        with socket.create_connection(('127.0.0.1', 7420), timeout=1):
            webbrowser.open(url)
            return
    except OSError:
        pass

    import control_server
    import led_fans
    import lyrics_source

    stop = threading.Event()
    worker = threading.Thread(target=led_fans.run, args=(stop,), daemon=True)
    worker.start()
    lyrics_source.start()
    control_server.start(set_led_fn=lambda key, value: control_server._apply_led(led_fans, key, value))
    if '--no-browser' not in sys.argv:
        webbrowser.open(url)
    print(f'Phosphor: {url} — Ctrl+C pour arrêter')
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        lyrics_source.stop()
        stop.set()
        worker.join(timeout=5)
        led_fans._flush_config()
        led_fans.shutdown()


if __name__ == '__main__':
    main()
