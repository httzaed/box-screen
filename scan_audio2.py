"""Scanne les devices Minifuse à 48000 Hz."""
import sounddevice as sd
import numpy as np

print("Lance de la musique!\n")
import time; time.sleep(1)

for idx, sr in [(2, 48000), (3, 48000), (4, 48000), (17, 48000), (4, 44100), (0, 44100)]:
    try:
        dev = sd.query_devices(idx)
        n_ch = min(2, dev["max_input_channels"])
        rec = sd.rec(int(sr * 2), samplerate=sr, channels=n_ch,
                     device=idx, dtype="float32", blocking=True)
        rms = float(np.sqrt(np.mean(rec**2)))
        bar = "█" * int(rms * 400)
        print(f"  [{idx}] {sr}Hz  {dev['name'][:35]:<35}  rms={rms:.5f}  {bar}")
    except Exception as e:
        print(f"  [{idx}] {sr}Hz  ERREUR: {e}")
