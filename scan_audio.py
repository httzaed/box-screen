"""Scanne tous les devices input pendant 2s et affiche le RMS."""
import sounddevice as sd
import numpy as np
import time

INDICES = [0, 1, 2, 3, 4, 13, 14, 15, 16, 17]
SR = 44100
DUR = 2.0

print("Lance de la musique maintenant !\n")
time.sleep(1)

for idx in INDICES:
    try:
        dev = sd.query_devices(idx)
        n_ch = min(2, dev["max_input_channels"])
        if n_ch == 0:
            continue
        frames = int(SR * DUR)
        rec = sd.rec(frames, samplerate=SR, channels=n_ch,
                     device=idx, dtype="float32", blocking=True)
        rms = float(np.sqrt(np.mean(rec**2)))
        bar = "█" * int(rms * 500)
        print(f"  [{idx:2d}] {dev['name'][:40]:<40}  rms={rms:.5f}  {bar}")
    except Exception as e:
        print(f"  [{idx:2d}] ERREUR: {e}")
