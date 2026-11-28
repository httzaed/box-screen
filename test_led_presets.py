"""
test_led_presets.py : fait défiler chaque preset de led_colors.py sur les LEDs
(fans + boîtier + clavier) pour comparer le rendu réel avec la couleur écran.

Box Screen doit tourner : le script passe par l'API locale du dashboard
(port 7420), donc il n'entre pas en conflit avec la boucle LED.
La config LED d'origine est remise à la fin, même après Ctrl+C.

Usage :
  python test_led_presets.py             Entrée = couleur suivante,
                                         ou tape une note puis Entrée
  python test_led_presets.py --auto 3    défilement auto toutes les 3 s
  python test_led_presets.py amber red   seulement ces presets

Les notes sont ajoutées dans led_colors_notes.txt.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from led_colors import COLORS

API = "http://127.0.0.1:7420"
NOTES = Path(__file__).parent / "led_colors_notes.txt"

# Ce que le test modifie, donc ce qu'il faut remettre à la fin.
RESTORE_KEYS = ("rhythm_mode", "brightness",
                "fan_mode", "case_mode", "keyboard_mode",
                "fan_color", "case_color", "keyboard_color")


def api_get(path):
    with urllib.request.urlopen(API + path, timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def api_led(key, value):
    url = "{}/api/led/{}/{}".format(API, urllib.parse.quote(key, safe=""),
                                   urllib.parse.quote(str(value), safe=""))
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        r.read()


def swatch(rgb):
    """Bloc de la couleur telle que l'écran l'affiche (ANSI truecolor)."""
    r, g, b = rgb
    return "\033[48;2;{};{};{}m        \033[0m".format(r, g, b)


def show(name):
    for zone in ("fan", "case", "keyboard"):
        api_led(zone + "_color", name)


def main():
    parser = argparse.ArgumentParser(description="Test visuel des presets LED")
    parser.add_argument("names", nargs="*", help="presets à tester (défaut : tous)")
    parser.add_argument("--auto", type=float, metavar="SEC",
                        help="défilement automatique, SEC secondes par couleur")
    args = parser.parse_args()

    if os.name == "nt":
        os.system("")  # active les couleurs ANSI dans la console Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    names = args.names or [n for n in COLORS if n != "black"]
    unknown = [n for n in names if n not in COLORS]
    if unknown:
        sys.exit("Presets inconnus : " + ", ".join(unknown))

    try:
        original = api_get("/api/status").get("led") or {}
    except Exception as e:
        sys.exit("Box Screen ne répond pas sur {} ({}). Lance-le d'abord.".format(API, e))
    if not original:
        sys.exit("Impossible de lire la config LED actuelle, test annulé.")

    try:
        if api_get("/api/ambience").get("enabled"):
            print("Attention : l'ambiance musique est active. Les couleurs de la")
            print("pochette peuvent écraser le test au changement de morceau.")
            if input("Continuer quand même ? (o/n) ").strip().lower() != "o":
                return
    except Exception:
        pass

    notes = []
    try:
        # Couleur fixe, pleine luminosité, sans effet de rythme.
        api_led("rhythm_mode", "legacy")
        api_led("brightness", 1.0)
        for zone in ("fan", "case", "keyboard"):
            api_led(zone + "_mode", "static")

        print()
        print("Le bloc à gauche = la couleur à l'écran. Compare avec les LEDs.")
        if not args.auto:
            print("Entrée = suivante. Tape une note puis Entrée pour la garder.")
        print("Ctrl+C pour arrêter.")
        print()

        for i, name in enumerate(names, 1):
            rgb = COLORS[name]
            show(name)
            label = "{} [{:>2}/{}] {:<11} {}".format(
                swatch(rgb), i, len(names), name, "{:>3},{:>3},{:>3}".format(*rgb))
            if args.auto:
                print(label)
                time.sleep(args.auto)
            else:
                note = input(label + "  > ").strip()
                if note:
                    notes.append((name, rgb, note))
    except (KeyboardInterrupt, EOFError):
        print("\nArrêt demandé.")
    finally:
        failed = []
        for key in RESTORE_KEYS:
            if key in original:
                try:
                    api_led(key, original[key])
                except Exception:
                    failed.append(key)
        if failed:
            print("Pas réussi à remettre : " + ", ".join(failed))
            print("Valeurs d'origine : " + json.dumps(
                {k: original[k] for k in failed}, ensure_ascii=False))
        else:
            print("Config LED d'origine remise.")

        if notes:
            with NOTES.open("a", encoding="utf-8") as f:
                f.write("\n# {}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M")))
                for name, rgb, note in notes:
                    f.write("{:<11} {:>3},{:>3},{:>3}  {}\n".format(name, *rgb, note))
            print("{} note(s) ajoutée(s) dans {}".format(len(notes), NOTES.name))


if __name__ == "__main__":
    main()
