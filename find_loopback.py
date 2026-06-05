"""Trouve le device WASAPI loopback pour les enceintes."""
import sounddevice as sd

print("Tous les devices (outputs inclus) :\n")
for i, d in enumerate(sd.query_devices()):
    print(f"  [{i:2d}] in={d['max_input_channels']} out={d['max_output_channels']}  {d['name']}")

print("\n--- Essai WASAPI loopback sur outputs ---")
import numpy as np, time

for i, d in enumerate(sd.query_devices()):
    if d['max_output_channels'] > 0 and 'High Definition' in d['name']:
        try:
            wasapi = sd.WasapiSettings(loopback=True)
            rec = sd.rec(int(44100 * 1.5), samplerate=44100,
                         channels=d['max_output_channels'],
                         device=i, dtype='float32',
                         extra_settings=wasapi, blocking=True)
            rms = float(np.sqrt(np.mean(rec**2)))
            print(f"  [{i}] {d['name']} → rms={rms:.5f}  {'✓ SIGNAL' if rms > 0.001 else 'silence'}")
        except Exception as e:
            print(f"  [{i}] {d['name']} → ERREUR: {e}")
