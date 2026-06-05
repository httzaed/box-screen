import sounddevice as sd
print("Tous les devices audio avec inputs:\n")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        print(f"  [{i}] {d['name']}")
