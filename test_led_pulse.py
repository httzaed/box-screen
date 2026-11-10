"""Deterministic kick simulation, without audio or OpenRGB hardware."""
import ast
import math
from pathlib import Path
import unittest


tree = ast.parse(Path(__file__).with_name('led_fans.py').read_text(encoding='utf-8'))
helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_pulse_envelope')
namespace = {'math': math}
exec(compile(ast.Module(body=[helper], type_ignores=[]), 'led_fans.py', 'exec'), namespace)
pulse = namespace['_pulse_envelope']


def simulate(bpm, fps=50):
    """An 80 ms bass envelope per kick, following the audio source's decay."""
    dt = 1 / fps
    period = 60 / bpm
    level = 0.0
    frames = []
    for i in range(round(4 * period * fps)):
        t = i * dt
        phase = t % period
        target = math.exp(-phase / 0.080)
        level = pulse(level, target, dt)
        frames.append((t, target, level))
    return frames


class PulseTests(unittest.TestCase):
    def test_attack_visible_in_first_frame(self):
        self.assertGreater(pulse(0, 1, 0.020), 0.90)

    def test_release_is_monotonic_and_reaches_black(self):
        level = 1.0
        for _ in range(30):
            following = pulse(level, 0, 0.020)
            self.assertLessEqual(following, level)
            level = following
        self.assertEqual(level, 0)

    def test_release_independent_of_frame_rate(self):
        levels = []
        for fps in (25, 50, 100):
            level = 1.0
            for _ in range(round(0.2 * fps)):
                level = pulse(level, 0, 1 / fps)
            levels.append(level)
        self.assertLess(max(levels) - min(levels), 1e-10)

    def test_kicks_remain_distinct(self):
        for bpm in (90, 120, 160):
            with self.subTest(bpm=bpm):
                frames = simulate(bpm)
                period = 60 / bpm
                peaks = [v for t, _, v in frames if t % period < 0.021]
                troughs = [v for t, _, v in frames if t % period > period - 0.041]
                # A kick may land just after a frame, losing up to 20 ms of its peak.
                self.assertGreater(min(peaks), 0.70)
                self.assertLess(max(troughs), 0.12)


if __name__ == '__main__':
    unittest.main()
