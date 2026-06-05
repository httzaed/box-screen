"""Test BPM — log dans bpm_log.txt toutes les 10s. Ctrl+C pour arrêter."""
import time, bpm_source

bpm_source.start()
LOG = "bpm_log.txt"
open(LOG, "w").close()
print("Lancé. Log dans bpm_log.txt toutes les 10s. Ctrl+C pour stop.")

try:
    while True:
        time.sleep(10)
        bpm = bpm_source.get_bpm()
        line = f"{time.strftime('%H:%M:%S')}  BPM = {bpm:.1f}" if bpm else f"{time.strftime('%H:%M:%S')}  BPM = None"
        print(line)
        open(LOG, "a").write(line + "\n")
except KeyboardInterrupt:
    bpm_source.stop()
