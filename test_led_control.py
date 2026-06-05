"""
test_led_control.py — Test direct du contrôle LED sans launcher.
Lance le thread LED et change les modes/couleurs interactivement.
"""
import threading, time
import led_fans

stop = threading.Event()

print("Démarrage thread LED...")
t = threading.Thread(target=led_fans.run, args=(stop,), daemon=True)
t.start()
time.sleep(1.5)

print(f"Config initiale: {led_fans.get_cfg()}")
print()
print("Commandes :")
print("  cm <mode>   → case mode  (off / wave / beat)")
print("  fm <mode>   → fan mode   (off / spin_bpm / spin_fixed / static)")
print("  cc <color>  → case color (violet/cyan/orange/green/red/white)")
print("  fc <color>  → fan color")
print("  q           → quitter")

while True:
    try:
        raw = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        break
    if raw == "q":
        break
    parts = raw.split()
    if len(parts) != 2:
        print("  syntaxe: cm off | fm spin_bpm | cc cyan ...")
        continue
    cmd, val = parts
    if cmd == "cm":
        led_fans.set_cfg(case_mode=val)
    elif cmd == "fm":
        led_fans.set_cfg(fan_mode=val)
    elif cmd == "cc":
        led_fans.set_cfg(case_color=val)
    elif cmd == "fc":
        led_fans.set_cfg(fan_color=val)
    else:
        print("  commande inconnue")
        continue
    print(f"  cfg: {led_fans.get_cfg()}")

stop.set()
led_fans.shutdown()
print("Arrêt.")
