import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import map_leds

class Zone:
    def __init__(self, name, size):
        self.name, self.leds = name, [None] * size
    def resize(self, size):
        self.leds = [None] * size

class Device:
    name = 'B550 EAGLE WIFI6'
    def __init__(self):
        self.zones = [Zone('D_LED1', 0), Zone('D_LED2', 0), Zone('LED_C1', 1)]
        self.frames = []
    @property
    def leds(self):
        return [led for zone in self.zones for led in zone.leds]
    def set_mode(self, mode):
        pass
    def update(self):
        pass
    def set_colors(self, colors, fast=False):
        assert fast, 'Le mapping ne doit pas relire OpenRGB a chaque couleur'
        assert len(colors) == len(self.leds)
        self.frames.append(colors)

class MapperTests(unittest.TestCase):
    def test_checkpoint_roundtrip_and_layout_mismatch(self):
        device = Device()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            with patch.object(map_leds, 'MAP_FILE', Path(folder) / 'led_map.json'):
                map_leds.save_progress(device, {0: 'fans'})
                self.assertEqual(map_leds.load_progress(device), {0: 'fans'})
                device.zones[0].resize(2)
                self.assertEqual(map_leds.load_progress(device), {})

    def test_cleanup_does_not_mask_connection_failure(self):
        device = types.SimpleNamespace(set_colors=lambda *args, **kwargs:
                                       (_ for _ in ()).throw(ConnectionError('offline')))
        map_leds.clear_leds(device, [])

    def test_identification_uses_current_zone_offsets(self):
        device = Device()
        device.zones[0].resize(2)
        device.zones[1].resize(3)
        data = map_leds.identify_device(device)
        self.assertEqual(data['led_count'], 6)
        self.assertEqual([z['offset'] for z in data['zones']], [0, 2, 5])
        self.assertEqual([led['index'] for led in data['zones'][1]['leds']], [2, 3, 4])

    def test_inconsistent_zone_counts_are_rejected(self):
        device = types.SimpleNamespace(name='board', leds=[None] * 3,
                                       zones=[Zone('ARGB', 2)])
        with self.assertRaises(ValueError):
            map_leds.identify_device(device)

    def test_preview_visits_every_address_and_clears_on_interrupt(self):
        device = Device()
        device.zones[0].resize(2)
        with patch.object(map_leds.time, 'sleep'):
            map_leds.preview_leds(device, lambda *rgb: rgb)
        white_indices = [[i for i, rgb in enumerate(frame) if rgb == (80, 80, 80)]
                         for frame in device.frames if (80, 80, 80) in frame]
        self.assertEqual(white_indices, [[0], [1], [2]])
        self.assertEqual(device.frames[-1], [(0, 0, 0)] * 3)
        with patch.object(map_leds.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                map_leds.preview_leds(device, lambda *rgb: rgb)
        self.assertEqual(device.frames[-1], [(0, 0, 0)] * 3)

    def test_cancel_during_preview_preserves_saved_mapping(self):
        device = Device()
        modules = {'openrgb': types.SimpleNamespace(OpenRGBClient=lambda:
                       types.SimpleNamespace(devices=[device])),
                   'openrgb.utils': types.SimpleNamespace(RGBColor=lambda *rgb: rgb)}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / 'led_map.json'
            path.write_text('{"old": true}')
            with patch.dict('sys.modules', modules), patch.object(map_leds, 'MAP_FILE', path), patch('builtins.input', side_effect=['', '', '']), patch.object(map_leds.time, 'sleep'), patch.object(map_leds, 'preview_leds', side_effect=KeyboardInterrupt):
                self.assertEqual(map_leds.main(), 0)
            self.assertEqual(json.loads(path.read_text()), {'old': True})

    def test_resize_two_outputs_and_detect_keyboard(self):
        device = Device()
        mouse = types.SimpleNamespace(name='G Pro Wireless Gaming Mouse', leds=[None]*2,
                                      zones=[Zone('Mouse', 2)])
        keyboard = types.SimpleNamespace(name='G915 TKL Keyboard', leds=[None]*87,
                                         zones=[Zone('Keys', 87)])
        client = types.SimpleNamespace(devices=[mouse, keyboard, device])
        modules = {'openrgb': types.SimpleNamespace(OpenRGBClient=lambda: client),
                   'openrgb.utils': types.SimpleNamespace(RGBColor=lambda *rgb: rgb)}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / 'led_map.json'
            path.write_text('{"old": true}')
            with patch.dict('sys.modules', modules), patch.object(map_leds, 'MAP_FILE', path), patch('builtins.input', side_effect=['', '15', '52', 'f', 'c', 's']), patch.object(map_leds.time, 'sleep'):
                self.assertEqual(map_leds.main(), 0)
            data = json.loads(path.read_text())
            self.assertEqual(data['groups']['fans'], list(range(15)))
            self.assertEqual(data['groups']['case'], list(range(15, 67)))
            self.assertEqual(data['keyboard']['device_idx'], 1)
            self.assertEqual(data['keyboard']['leds'], 87)
            self.assertEqual(data['identification']['led_count'], 68)
            self.assertEqual(data['identification']['zones'][1]['offset'], 15)
            self.assertEqual(json.loads(path.with_suffix('.json.bak').read_text()), {'old': True})

    def test_stale_indices_are_not_saved(self):
        data = map_leds.build_result(Device(), {0: 'fans', 99: 'case'})
        self.assertEqual(data['groups'], {'fans': [0]})

    def test_all_skipped_preserves_saved_mapping_and_backup(self):
        device = Device()
        modules = {'openrgb': types.SimpleNamespace(OpenRGBClient=lambda:
                       types.SimpleNamespace(devices=[device])),
                   'openrgb.utils': types.SimpleNamespace(RGBColor=lambda *rgb: rgb)}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / 'led_map.json'
            path.write_text('{"old": true}')
            backup = path.with_suffix('.json.bak')
            backup.write_text('{"backup": true}')
            with patch.dict('sys.modules', modules), patch.object(map_leds, 'MAP_FILE', path), patch('builtins.input', side_effect=['', '', '', 's']), patch.object(map_leds.time, 'sleep'):
                self.assertEqual(map_leds.main(), 1)
            self.assertEqual(json.loads(path.read_text()), {'old': True})
            self.assertEqual(json.loads(backup.read_text()), {'backup': True})

if __name__ == '__main__':
    unittest.main()
