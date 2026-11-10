"""Restart launcher after a crash or a stalled LCD transfer."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
STARTUP_GRACE = 60
FRAME_TIMEOUT = 30


def announce(message):
    if sys.stdout is not None:
        print(message, flush=True)


def mirror_output(stream, log):
    """Keep the terminal visible while retaining restart diagnostics on disk."""
    try:
        for line in stream:
            log.write(line)
            log.flush()
            if sys.stdout is not None:
                try:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                except (OSError, UnicodeError):
                    pass
    finally:
        stream.close()


def stalled(heartbeat, started, now):
    if now - started < STARTUP_GRACE:
        return False
    try:
        return now - heartbeat.stat().st_mtime > FRAME_TIMEOUT
    except FileNotFoundError:
        return True


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main(args=None):
    # Prevent two supervised launchers from accessing the same USB panel.
    mutex = socket.socket()
    if os.name == 'nt':
        mutex.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        mutex.bind(('127.0.0.1', 7422))
    except OSError:
        mutex.close()
        announce('Launcher est deja surveille (port 7422 occupe).')
        return 0
    child = None
    delay = 1
    try:
        with tempfile.TemporaryDirectory(prefix='.launcher-', dir=ROOT) as tmp:
            heartbeat = Path(tmp) / 'frame-heartbeat'
            env = dict(os.environ, BOX_SCREEN_HEARTBEAT=str(heartbeat),
                       PYTHONIOENCODING='utf-8')
            while True:
                heartbeat.unlink(missing_ok=True)
                started = time.time()
                with (ROOT / 'launcher-watchdog.log').open('a', encoding='utf-8') as log:
                    log.write(time.strftime('\n%Y-%m-%d %H:%M:%S') + ' Demarrage launcher\n')
                    log.flush()
                    announce('Demarrage launcher — surveillance active. Ctrl+C pour arreter.')
                    child = subprocess.Popen(
                        [sys.executable, '-u', str(ROOT / 'launcher.py'),
                         '--launcher-child', *(args or [])],
                        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding='utf-8', errors='replace')
                    reader = threading.Thread(target=mirror_output, args=(child.stdout, log), daemon=True)
                    reader.start()
                    try:
                        while child.poll() is None:
                            time.sleep(2)
                            if stalled(heartbeat, started, time.time()):
                                announce('Connexion ecran bloquee : redemarrage launcher.')
                                stop_child(child)
                                break
                    finally:
                        if child.poll() is None:
                            stop_child(child)
                        reader.join()
                    code = child.wait()
                    # Normal shutdown, including Ctrl+C delivered to the child.
                    if code in (0, 130, -1073741510, 3221225786):
                        return 0
                    log.write(f'Processus termine ({code}), relance dans {delay}s\n')
                    announce(f'Launcher arrete ({code}). Nouvelle tentative dans {delay}s.')
                if time.time() - started >= 120:
                    delay = 1
                time.sleep(delay)
                delay = min(10, delay * 2)
    except KeyboardInterrupt:
        return 0
    finally:
        if child is not None:
            stop_child(child)
        mutex.close()

