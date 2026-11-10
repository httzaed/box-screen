"""Test G915 seul: arreter launcher.py puis python3 diagnose_keyboard.py."""
import time
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor
from led_fans import _select_keyboard, _prepare_keyboard


def main():
    client = OpenRGBClient()
    keyboard = _select_keyboard(client.devices)
    if keyboard is None:
        raise RuntimeError('G915 introuvable dans OpenRGB')
    print(f'Clavier: {keyboard.name}, {len(keyboard.leds)} LEDs')
    print('Arreter launcher.py et quitter G HUB pendant ce test.')
    _prepare_keyboard(keyboard)
    for label, rgb in [('rouge', (255, 0, 0)), ('vert', (0, 255, 0)), ('bleu', (0, 0, 255))]:
        print(f'Direct: {label} (3 secondes)', flush=True)
        keyboard.set_colors([RGBColor(*rgb) for _ in keyboard.leds])
        time.sleep(3)
    direct = input('Les touches se sont allumees en Direct ? oui/non > ').strip()
    static = next((m for m in keyboard.modes if m.name.lower() == 'static'), None)
    if static is not None:
        keyboard.set_mode(static, force=True)
        time.sleep(0.2)
        keyboard.set_color(RGBColor(255, 255, 255))
        print('Static: blanc. Appuyer sur une touche pour reveiller le clavier.', flush=True)
        time.sleep(3)
        visible = input('Les touches sont blanches en Static ? oui/non > ').strip()
    else:
        visible = 'mode absent'
    print(f'RESULTAT: Direct={direct}; Static={visible}')
    print('Copier ce resultat et les lignes Mode clavier dans le chat.')


if __name__ == '__main__':
    main()
