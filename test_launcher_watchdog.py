import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
import io
import subprocess
import os

from launcher_watchdog import stalled, stop_child, mirror_output


class LauncherWatchdogTests(unittest.TestCase):
    def test_child_output_is_visible_and_logged(self):
        stream = io.StringIO('Panel connecte\nErreur USB\n')
        log = io.StringIO()
        console = io.StringIO()
        with patch('sys.stdout', console):
            mirror_output(stream, log)
        self.assertEqual(console.getvalue(), 'Panel connecte\nErreur USB\n')
        self.assertEqual(log.getvalue(), console.getvalue())

    def test_background_output_without_terminal(self):
        log = io.StringIO()
        with patch('sys.stdout', None):
            mirror_output(io.StringIO('Demarrage\n'), log)
        self.assertEqual(log.getvalue(), 'Demarrage\n')

    def test_startup_grace(self):
        self.assertFalse(stalled(Path('missing-heartbeat'), 100, 159))

    def test_missing_frame_after_startup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            self.assertTrue(stalled(Path(tmp) / 'missing', 100, 161))

    def test_frame_timeout_and_recovery(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            heartbeat = Path(tmp) / 'frame'
            heartbeat.touch()
            os.utime(heartbeat, (150, 150))
            self.assertFalse(stalled(heartbeat, 100, 179))
            self.assertTrue(stalled(heartbeat, 100, 181))
            os.utime(heartbeat, (181, 181))
            self.assertFalse(stalled(heartbeat, 100, 182))

    def test_blocked_process_is_killed(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('launcher', 5), 1]
        stop_child(child)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()

