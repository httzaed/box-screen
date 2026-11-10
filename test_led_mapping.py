import ast
import pathlib
import types
import unittest

# Exercise validation without importing OpenRGB or contacting hardware.
source = pathlib.Path('led_fans.py').read_text(encoding='utf-8')
tree = ast.parse(source)
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_mapped_indices')
namespace = dict(ZONE_OFFSET=5, FAN_IDXS=list(range(15)), CASE_IDXS=list(range(15, 67)), MB_IDXS=[])
exec(compile(ast.Module(body=[function], type_ignores=[]), 'led_fans.py', 'exec'), namespace)

class MappingTests(unittest.TestCase):
    def test_old_layout_on_three_led_board(self):
        device = types.SimpleNamespace(name='B550 EAGLE WIFI6', leds=[None] * 3)
        self.assertEqual(namespace['_mapped_indices'](device), ([], []))

    def test_compatible_layout(self):
        device = types.SimpleNamespace(name='ASUS TUF', leds=[None] * 125)
        self.assertEqual(namespace['_mapped_indices'](device), (list(range(15)), list(range(15, 67))))

    def test_negative_offset_is_rejected(self):
        previous = namespace['ZONE_OFFSET']
        try:
            namespace['ZONE_OFFSET'] = -1
            device = types.SimpleNamespace(name='board', leds=[None] * 125)
            self.assertEqual(namespace['_mapped_indices'](device), ([], []))
        finally:
            namespace['ZONE_OFFSET'] = previous

if __name__ == '__main__':
    unittest.main()
