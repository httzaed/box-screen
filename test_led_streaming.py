"""Exercise the production frame sender without connected LED hardware."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


tree = ast.parse(Path(__file__).with_name('led_fans.py').read_text(encoding='utf-8'))
helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_send_frame')
namespace = {}
exec(compile(ast.Module(body=[helper], type_ignores=[]), 'led_fans.py', 'exec'), namespace)
send_frame = namespace['_send_frame']


class StreamingTests(unittest.TestCase):
    def test_animation_never_requests_device_state(self):
        calls = []
        def device(name):
            def write(colors, fast=False):
                if not fast:
                    raise TimeoutError('Device-state read timed out')
                calls.append((name, colors))
            return SimpleNamespace(set_colors=write)
        board, keyboard = device('board'), device('keyboard')
        for level in (0.0, 1.0, 0.5, 0.1):
            self.assertIsNone(send_frame(board, [level], keyboard, [level]))
        self.assertEqual([name for name, _ in calls], ['board', 'keyboard'] * 4)

    def test_keyboard_disconnect_does_not_abort_board_frames(self):
        board = SimpleNamespace(set_colors=Mock())
        error = TimeoutError('keyboard disconnected')
        keyboard = SimpleNamespace(set_colors=Mock(side_effect=error))
        self.assertIs(send_frame(board, [1], keyboard, [1]), error)
        self.assertIsNone(send_frame(board, [0.5], None, None))
        self.assertIsNone(send_frame(board, [0.1], None, None))
        self.assertEqual(board.set_colors.call_count, 3)

    def test_board_failure_still_triggers_outer_reconnection(self):
        board = SimpleNamespace(set_colors=Mock(side_effect=ConnectionError('board disconnected')))
        with self.assertRaises(ConnectionError):
            send_frame(board, [1], None, None)

    def test_keyboard_only(self):
        keyboard = SimpleNamespace(set_colors=Mock())
        self.assertIsNone(send_frame(None, [], keyboard, [1]))
        keyboard.set_colors.assert_called_once_with([1], fast=True)


if __name__ == '__main__':
    unittest.main()
