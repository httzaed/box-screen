"""Album colours and persistent lighting scenes, shared by HTTP and hotkeys."""
import asyncio
import copy
import io
import json
import threading
from pathlib import Path

_FILE = Path(__file__).with_name('ambience_settings.json')
_lock = threading.RLock()
_settings = {'music': False, 'active': 'default', 'saved': {}}
_art = {'track': '', 'colors': [], 'status': 'En attente de pochette'}
_started = False
_global_hotkeys = False
SCENES = {
    'tournage': dict(brightness=.65, rhythm_mode='legacy', case_mode='static', fan_mode='static', keyboard_mode='static', case_color='#ff287f', fan_color='#ff8c28', keyboard_color='#ffd8a8'),
    'montage': dict(brightness=.25, rhythm_mode='legacy', case_mode='static', fan_mode='static', keyboard_mode='static', case_color='#ffd8a8', fan_color='#ffd8a8', keyboard_color='#ffffff'),
    'st': dict(brightness=.8, rhythm_mode='legacy', case_mode='dual', fan_mode='static', keyboard_mode='static', case_color='#ff0080', case_color2='#ff7800', fan_color='#ff7800', keyboard_color='#ff0080'),
    'soiree': dict(brightness=1., rhythm_mode='split', case_mode='beat', fan_mode='beat_pulse', keyboard_mode='beat_pulse', case_color='#b428ff', case_color2='#00c8ff', fan_color='#ff0080', fan_color2='#ff7800', keyboard_color='#00c8ff', keyboard_color2='#b428ff'),
}

def _save():
    tmp = _FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(_settings, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(_FILE)

def snapshot():
    with _lock:
        return copy.deepcopy({**_art, 'enabled': _settings['music'], 'active': _settings['active'], 'custom_saved': 'autre' in _settings['saved'], 'global_hotkeys': _global_hotkeys})

def _apply_colors():
    if _settings['music'] and len(_art['colors']) >= 2:
        import led_fans
        a, b = _art['colors'][:2]
        led_fans.set_cfg(**{f'{z}_color{suffix}': color for z in ('case', 'fan', 'keyboard') for suffix, color in (('', a), ('2', b))})

def action(name):
    import led_fans
    with _lock:
        if name == 'save-autre':
            _settings['saved']['autre'] = {'led': led_fans.get_cfg(), 'music': _settings['music']}
            _settings['active'] = 'autre'
        elif name in ('music-on', 'music-off'):
            _settings['music'] = name == 'music-on'
            _settings['active'] = ''
            _apply_colors()
        elif name in ('default', 'autre') or name in SCENES:
            if name == 'autre' and name not in _settings['saved']:
                return {'ok': False, 'error': 'Enregistre d’abord ton ambiance avec Mémoriser.'}
            saved = _settings['saved'].get(name)
            config = saved['led'] if saved else SCENES[name]
            led_fans.set_cfg(**config)
            _settings['music'] = saved.get('music', False) if saved else name == 'soiree'
            _settings['active'] = name
            _apply_colors()
        else:
            return {'ok': False, 'error': 'Ambiance inconnue'}
        _save()
        return {'ok': True, **snapshot()}

def extract_colors(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as source:
        img = source.convert('RGB')
        img.thumbnail((96, 96))
        quant = img.quantize(colors=8)
        palette = quant.getpalette()
        ranked = sorted(quant.getcolors(), reverse=True)
        colors = [tuple(palette[i*3:i*3+3]) for _, i in ranked]
        visible = [c for c in colors if 35 < max(c) and min(c) < 235]
        colors = visible or colors
        primary = colors[0]
        secondary = max(colors, key=lambda c: sum((a-b)**2 for a,b in zip(primary,c)))
        return ['#%02x%02x%02x' % c for c in (primary, secondary)]

async def _read_art():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTC
    from winrt.windows.storage.streams import DataReader
    manager = await SMTC.request_async()
    session = manager.get_current_session()
    if session is None:
        return '', None
    props = await session.try_get_media_properties_async()
    track = f'{props.artist} — {props.title}' if props.artist else props.title
    with _lock:
        if track == _art['track'] and _art['colors']:
            return track, 'cached'
    if props.thumbnail is None:
        return track, None
    stream = await props.thumbnail.open_read_async()
    try:
        if stream.size > 8 * 1024 * 1024:
            return track, None
        reader = DataReader(stream)
        try:
            await reader.load_async(stream.size)
            data = bytearray(stream.size)
            reader.read_bytes(data)
            return track, extract_colors(data)
        finally:
            reader.detach_stream()
            reader.close()
    finally:
        stream.close()

def _worker():
    while True:
        try:
            track, colors = asyncio.run(asyncio.wait_for(_read_art(), timeout=5))
            if colors != 'cached':
                with _lock:
                    _art.update(track=track, colors=colors or [], status='Couleurs de la pochette' if colors else 'Pochette indisponible' if track else 'En attente de musique')
                    _apply_colors()
        except Exception:
            with _lock:
                _art.update(track='', colors=[], status='Pochette indisponible')
        threading.Event().wait(5)

def start():
    global _started, _global_hotkeys
    with _lock:
        if _started:
            return
        import led_fans
        if _FILE.exists():
            try:
                _settings.update(json.loads(_FILE.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                pass
        if 'default' not in _settings['saved']:
            _settings['saved']['default'] = {'led': led_fans.get_cfg(), 'music': False}
            _save()
        _started = True
    threading.Thread(target=_worker, daemon=True, name='album-colors').start()
    try:
        from windows_hotkeys import register
        scenes = ('default', 'tournage', 'montage', 'st', 'soiree', 'autre')
        ctrl_alt = 0x0002 | 0x0001
        _global_hotkeys = register([
            (ctrl_alt, 0x70 + index, lambda n=name: action(n))
            for index, name in enumerate(scenes)
        ])
    except Exception as exc:
        print(f'[Ambiances] Raccourcis globaux indisponibles: {exc}')
