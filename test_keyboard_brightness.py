import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

source = ast.parse(Path('led_fans.py').read_text(encoding='utf-8'))
helper = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == '_prepare_keyboard')
ns = {'time': SimpleNamespace(sleep=lambda seconds: None)}
exec(compile(ast.Module(body=[helper], type_ignores=[]), 'led_fans.py', 'exec'), ns)

class KeyboardBrightnessTests(unittest.TestCase):
    def check_limits(self, minimum, maximum, changes):
        mode = SimpleNamespace(name='Direct', brightness=None, brightness_min=minimum, brightness_max=maximum)
        kb = SimpleNamespace(modes=[mode], active_mode=0, set_custom_mode=Mock(), set_mode=Mock())
        ns['_prepare_keyboard'](kb)
        kb.set_custom_mode.assert_called_once()
        if changes:
            self.assertEqual(mode.brightness, maximum)
            kb.set_mode.assert_called_once_with(mode, force=True)
        else:
            kb.set_mode.assert_called_once_with(mode, force=True)

    def test_g915_none_limits(self):
        self.check_limits(None, None, False)

    def test_numeric_limits(self):
        self.check_limits(0, 100, True)

    def test_partial_limits(self):
        self.check_limits(None, 100, False)

    def test_fixed_brightness(self):
        self.check_limits(0, 0, False)

if __name__ == '__main__':
    unittest.main()
