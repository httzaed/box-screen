"""Configure les zones ARGB et cartographie fans/boitier, puis sauve led_map.json."""
import json
import time
from pathlib import Path

MAP_FILE = Path(__file__).with_name('led_map.json')
TAGS = {'f': 'fans', 'c': 'case', 'm': 'mainboard', 's': 'skip'}


def progress_file():
    return MAP_FILE.with_suffix('.progress.json')


def save_progress(device, mapping):
    path = progress_file()
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(build_result(device, mapping), indent=2), encoding='utf-8')
    temporary.replace(path)


def load_progress(device):
    path = progress_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        zones = [{'name': z.name, 'size': len(z.leds)} for z in device.zones]
        if data['device'] != device.name or data['zones'] != zones:
            print('Brouillon incompatible avec les zones actuelles: reprise ignoree.')
            return {}
        mapping = {int(i): tag for i, tag in data['mapping'].items()}
        if any(not 0 <= i < len(device.leds) or tag not in TAGS.values()
               for i, tag in mapping.items()):
            raise ValueError('indices ou groupes invalides')
        print(f'Reprise automatique: {len(mapping)} LEDs deja attribuees.')
        return mapping
    except (ValueError, KeyError, TypeError) as error:
        print(f'Brouillon illisible: {error}. Reprise ignoree.')
        return {}


def clear_leds(device, black):
    try:
        device.set_colors(black, fast=True)
    except Exception as error:
        print(f'Extinction impossible (connexion OpenRGB): {type(error).__name__}.')


def identify_device(device):
    """Inventory SDK addresses, without claiming ARGB provides physical feedback."""
    zones = []
    offset = 0
    for zone_id, zone in enumerate(device.zones):
        count = len(zone.leds)
        zones.append({
            'zone_idx': zone_id, 'name': zone.name, 'offset': offset,
            'size': count,
            'leds': [{'index': offset + i, 'zone_index': i,
                      'name': getattr(led, 'name', None) or f'LED {i}'}
                     for i, led in enumerate(zone.leds)],
        })
        offset += count
    if offset != len(device.leds):
        raise ValueError(f'{device.name}: les zones exposent {offset} LEDs, '
                         f'le peripherique en expose {len(device.leds)}. '
                         'Mapping interrompu pour eviter des indices incorrects.')
    return {'device': device.name, 'led_count': len(device.leds), 'zones': zones}


def print_inventory(inventory):
    print(f"\n{inventory['device']}: {inventory['led_count']} adresses LED declarees")
    for zone in inventory['zones']:
        count, offset = zone['size'], zone['offset']
        addresses = f"indices {offset}-{offset + count - 1}" if count else 'vide'
        print(f"  Zone {zone['zone_idx']} {zone['name']}: {count} LEDs ({addresses})")


def preview_leds(device, rgb_color):
    """Automatically show each configured zone, then each individual address."""
    inventory = identify_device(device)
    black = [rgb_color(0, 0, 0) for _ in device.leds]
    print('\nReperage automatique: zones en bleu, puis LEDs une par une en blanc.')
    print('Observer le boitier. Ctrl+C annule sans remplacer le mapping.')
    print('ARGB ne renvoie pas le nombre physique de LEDs ni leur emplacement:')
    print('les adresses declarees peuvent etre inutilisees ou partagees par plusieurs fans.')
    try:
        for zone in inventory['zones']:
            if not zone['size']:
                continue
            offset, count = zone['offset'], zone['size']
            print(f"  Reperage {zone['name']}: indices {offset}-{offset + count - 1}", flush=True)
            colors = list(black)
            colors[offset:offset + count] = [rgb_color(0, 0, 80) for _ in range(count)]
            device.set_colors(colors, fast=True)
            time.sleep(0.7)
            for led in zone['leds']:
                colors = list(black)
                colors[led['index']] = rgb_color(80, 80, 80)
                device.set_colors(colors, fast=True)
                print(f"    {zone['name']} / {led['name']} / index {led['index']}", flush=True)
                time.sleep(0.08)
    finally:
        clear_leds(device, black)
    return inventory


def ask_int(prompt, minimum, maximum, default=None):
    while True:
        raw = input(prompt).strip()
        try:
            value = default if not raw and default is not None else int(raw)
            if minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f'Entrer un nombre entre {minimum} et {maximum}.')


def build_result(device, mapping, keyboard=None, keyboard_idx=None):
    groups = {}
    for index, tag in sorted(mapping.items()):
        if 0 <= index < len(device.leds):
            groups.setdefault(tag, []).append(index)
    result = {
        'device': device.name, 'zone_idx': None, 'zone_offset': 0,
        'zone_size': len(device.leds),
        'zones': [{'name': z.name, 'size': len(z.leds)} for z in device.zones],
        'mapping': {str(i): tag for tag, indices in groups.items() for i in indices},
        'groups': groups,
        'identification': identify_device(device),
    }
    if keyboard is not None:
        result['keyboard'] = {'device_idx': keyboard_idx, 'name': keyboard.name,
                              'leds': len(keyboard.leds)}
    return result


def _run():
    from openrgb import OpenRGBClient
    from openrgb.utils import RGBColor
    try:
        client = OpenRGBClient()
    except Exception as error:
        print(f'OpenRGB inaccessible: {error}. Activer le SDK Server (port 6742).')
        return 1
    devices = client.devices
    if not devices:
        print('Aucun peripherique OpenRGB detecte.')
        return 1
    for i, device in enumerate(devices):
        print(f'{i}: {device.name} ({len(device.leds)} LEDs)')
        print_inventory(identify_device(device))
    print('\nInventaire automatique termine (configuration exposee par OpenRGB).')
    print('Arreter launcher.py pendant le reperage et le mapping pour eviter les animations concurrentes.')
    default = next((i for i, d in enumerate(devices) if 'B550 EAGLE' in d.name.upper()), None)
    selected = ask_int(f'Carte mere [{default}]: ', 0, len(devices) - 1, default)
    device = devices[selected]
    try:
        device.set_mode('direct')
    except Exception:
        device.set_mode('Direct')
    time.sleep(0.1)

    print('\nConfigurer le nombre de LEDs de chaque sortie D_LED (ARGB).')
    print('Fans en parallele: nombre de LEDs d UN fan. En serie: somme des LEDs.')
    print('Entrer 0 pour une sortie ARGB inutilisee. Les sorties LED_C/CPU restent intactes.')
    for zone_id in range(len(device.zones)):
        zone = device.zones[zone_id]
        print(f'Zone {zone_id}: {zone.name}, {len(zone.leds)} LEDs')
        if not zone.name.upper().startswith('D_LED'):
            continue
        size = ask_int(f'  Nombre de LEDs sur {zone.name} [{len(zone.leds)}]: ', 0, 10000, len(zone.leds))
        if size != len(zone.leds):
            try:
                zone.resize(size)
                time.sleep(0.1)
                device.update()
            except Exception as error:
                print(f'Redimensionnement impossible: {error}. Verifier Edit Zone dans OpenRGB.')
                return 1
            if len(device.zones[zone_id].leds) != size:
                print('OpenRGB a refuse cette taille. Mapping non sauvegarde.')
                return 1

    keyboard_idx = next((i for i, d in enumerate(devices)
                         if 'G915' in d.name.upper() or 'KEYBOARD' in d.name.upper()), None)
    keyboard = devices[keyboard_idx] if keyboard_idx is not None else None
    print(f'\nClavier: {keyboard.name if keyboard else "non detecte"}')
    print_inventory(identify_device(device))
    mapping = load_progress(device)
    if not mapping:
        preview_leds(device, RGBColor)
    print('\nReperage termine. Attribution des zones aux elements physiques:')
    print('Chaque zone est allumee en vert. f=fans, c=case, m=carte mere, s=ignorer,')
    print('l=identifier LED par LED dans cette zone, q=annuler sans sauvegarder.')
    black = [RGBColor(0, 0, 0) for _ in device.leds]
    offset = 0
    try:
        for zone in device.zones:
            count = len(zone.leds)
            if not count:
                continue
            if all(offset + i in mapping for i in range(count)):
                print(f'Zone {zone.name}: deja attribuee, conservee.')
                offset += count
                continue
            colors = list(black)
            colors[offset:offset + count] = [RGBColor(0, 100, 0) for _ in range(count)]
            device.set_colors(colors, fast=True)
            partial = any(offset + i in mapping for i in range(count))
            raw = 'l' if partial else input(f'Zone {zone.name} ({count} LEDs): f/c/m/s/l/q > ').strip().lower()
            while raw not in (*TAGS, 'l', 'q'):
                raw = input('Choisir f/c/m/s/l/q > ').strip().lower()
            if raw == 'q':
                print('Mapping annule, fichier existant conserve.')
                return 0
            if raw == 'l':
                last = 's'
                for index in range(count):
                    if offset + index in mapping:
                        last = next(key for key, value in TAGS.items()
                                    if value == mapping[offset + index])
                        continue
                    colors = list(black)
                    colors[offset + index] = RGBColor(0, 100, 0)
                    device.set_colors(colors, fast=True)
                    tag = input(f'  {zone.name} LED {index}: f/c/m/s, Entree={last}, q=annuler > ').strip().lower()
                    while tag not in (*TAGS, '', 'q'):
                        tag = input('Choisir f/c/m/s/q > ').strip().lower()
                    if tag == 'q':
                        return 0
                    last = tag or last
                    mapping[offset + index] = TAGS[last]
                    save_progress(device, mapping)
            else:
                mapping.update({offset + i: TAGS[raw] for i in range(count)})
                save_progress(device, mapping)
            offset += count
    except (EOFError, KeyboardInterrupt):
        print('\nMapping annule, fichier existant conserve.')
        return 0
    finally:
        clear_leds(device, black)

    result = build_result(device, mapping, keyboard, keyboard_idx)
    if not any(result['groups'].get(tag) for tag in ('fans', 'case', 'mainboard')):
        print('Aucune LED attribuee: mapping non sauvegarde, fichier existant conserve.')
        print('Reprendre le reperage et attribuer les LEDs visibles aux fans ou au boitier.')
        return 1
    if MAP_FILE.exists():
        MAP_FILE.with_suffix('.json.bak').write_bytes(MAP_FILE.read_bytes())
    temporary = MAP_FILE.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(MAP_FILE)
    progress_file().unlink(missing_ok=True)
    print(f'\nSauvegarde: {MAP_FILE}')
    for tag, indices in result['groups'].items():
        print(f'  {tag}: {len(indices)} LEDs')
    print('Relancer launcher.py pour charger le nouveau mapping.')
    return 0


def main():
    try:
        return _run()
    except (EOFError, KeyboardInterrupt):
        print('\nMapping annule, fichier existant conserve.')
        return 0
    except ValueError as error:
        print(f'Identification impossible: {error}')
        return 1
    except Exception as error:
        print(f'Operation interrompue: {type(error).__name__}: {error}')
        print('Verifier le serveur SDK OpenRGB puis relancer map_leds.py.')
        print('Les reponses deja enregistrees seront reprises si les zones sont identiques.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
