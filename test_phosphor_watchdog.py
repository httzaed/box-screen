import unittest
from unittest.mock import Mock, patch
import subprocess
import phosphor_watchdog as watchdog


class WatchdogTests(unittest.TestCase):
    def test_unreachable_service(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = OSError('offline')
            self.assertIsNone(watchdog.health())

    def test_kills_blocked_child(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('phosphor', 5), 0]
        watchdog.stop_child(child)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()

    def test_exited_child_is_not_killed(self):
        child = Mock()
        child.poll.return_value = 1
        watchdog.stop_child(child)
        child.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
