"""Supervise Phosphor. Ctrl+C stops both supervisor and child process."""
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:7420'


def health():
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                URL + '/api/health', timeout=2) as response:
            data = json.load(response)
            return data if data.get('service') == 'phosphor' else None
    except (OSError, ValueError):
        return None


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main():
    # Hold a local mutex for the entire supervisor lifetime, including restarts.
    import socket
    mutex = socket.socket()
    try:
        mutex.bind(('127.0.0.1', 7421))
    except OSError:
        print('Une surveillance Phosphor est deja active.')
        return
    child = None
    opened = False
    delay = 1
    try:
        if health():
            webbrowser.open(URL)
            print('Phosphor est deja actif. Pour activer la surveillance, fermez-le puis relancez ce fichier.')
            return
        while True:
            started = time.monotonic()
            with (ROOT / 'phosphor-watchdog.log').open('a', encoding='utf-8') as log:
                log.write(time.strftime('\n%Y-%m-%d %H:%M:%S') + ' Demarrage Phosphor\n')
                log.flush()
                child = subprocess.Popen(
                    [sys.executable, '-u', str(ROOT / 'phosphor.py'), '--no-browser'],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                failures = 0
                while child.poll() is None:
                    time.sleep(5)
                    state = health()
                    healthy = bool(state and state.get('ok') and state.get('pid') == child.pid)
                    if healthy:
                        failures = 0
                        if not opened:
                            webbrowser.open(URL)
                            opened = True
                    elif time.monotonic() - started >= 30:
                        failures += 1
                    if failures >= 3:
                        print('Phosphor ne repond plus : redemarrage.')
                        stop_child(child)
                        break
            if time.monotonic() - started >= 120:
                delay = 1
            print(f'Relance dans {delay} seconde(s). Ctrl+C pour arreter.')
            time.sleep(delay)
            delay = min(10, delay * 2)
    except KeyboardInterrupt:
        print('Arret de Phosphor et de sa surveillance.')
    finally:
        if child is not None:
            stop_child(child)
        mutex.close()


if __name__ == '__main__':
    main()
