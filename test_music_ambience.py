import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import music_ambience as ambience
from PIL import Image


class AmbienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.cfg = {'brightness': .43, 'case_color': '#123456', 'case_mode': 'dual'}
        self.settings = {'music': False, 'active': 'default', 'saved': {'default': {'led': dict(self.cfg), 'music': False}}}
        self.patches = [patch.object(ambience, '_FILE', Path(self.temp.name) / 'settings.json'),
                        patch.object(ambience, '_settings', self.settings),
                        patch.object(ambience, '_art', {'colors': [], 'track': '', 'status': ''}),
                        patch.dict('sys.modules', {'led_fans': SimpleNamespace(get_cfg=lambda: dict(self.cfg), set_cfg=lambda **kw: self.cfg.update(kw))})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_default_restores_original_settings(self):
        ambience.action('tournage')
        self.assertEqual(self.cfg['case_mode'], 'static')
        ambience.action('default')
        self.assertEqual(self.cfg['brightness'], .43)
        self.assertEqual(self.cfg['case_color'], '#123456')

    def test_custom_requires_save_and_restores(self):
        self.assertFalse(ambience.action('autre')['ok'])
        ambience.action('save-autre')
        ambience.action('montage')
        ambience.action('autre')
        self.assertEqual(self.cfg['brightness'], .43)

    def test_no_art_keeps_colors_and_toggle_applies_actual_palette(self):
        ambience.action('music-on')
        self.assertEqual(self.cfg['case_color'], '#123456')
        ambience._art['colors'] = ['#ff0000', '#0000ff']
        ambience.action('music-on')
        self.assertEqual(self.cfg['keyboard_color2'], '#0000ff')
        ambience.action('tournage')
        self.assertFalse(ambience.snapshot()['enabled'])

    def test_extract_two_dominant_colors(self):
        image = Image.new('RGB', (80, 80), 'red')
        image.paste('blue', (40, 0, 80, 80))
        data = io.BytesIO()
        image.save(data, format='PNG')
        self.assertEqual(set(ambience.extract_colors(data.getvalue())), {'#ff0000', '#0000ff'})


if __name__ == '__main__':
    unittest.main()
