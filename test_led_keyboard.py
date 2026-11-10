import ast
import pathlib
import types
import unittest

source = pathlib.Path('led_fans.py').read_text(encoding='utf-8')
tree = ast.parse(source)
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_select_keyboard')
namespace = {}
exec(compile(ast.Module(body=[function], type_ignores=[]), 'led_fans.py', 'exec'), namespace)
select = namespace['_select_keyboard']

class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.mouse = types.SimpleNamespace(name='G Pro Wireless Gaming Mouse')
        self.keyboard = types.SimpleNamespace(name='G915 TKL LIGHTSPEED Wireless RGB Mechanical Gaming Keyboard')
        self.board = types.SimpleNamespace(name='B550 EAGLE WIFI6')
        self.devices = [self.mouse, self.keyboard, self.board]

    def test_missing_mapping_discovers_g915(self):
        self.assertIs(select(self.devices), self.keyboard)

    def test_old_keyboard_name_discovers_g915_tkl(self):
        self.assertIs(select(self.devices, 'Logitech G915 Wireless RGB Mechanical Gaming Keyboard'), self.keyboard)

    def test_mouse_is_not_used_when_keyboard_absent(self):
        self.assertIsNone(select([self.mouse, self.board]))

if __name__ == '__main__':
    unittest.main()
