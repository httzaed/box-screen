"""Regression check: a slow BPM lookup must not pause beat processing."""
import ast
import collections
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch


class BeatLatencyTests(unittest.TestCase):
    def test_dsp_continues_during_lookup(self):
        tree = ast.parse(Path(__file__).with_name('bpm_source.py').read_text(encoding='utf-8'))
        detector = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == '_detector_loop')
        # Exercise the real scheduling without audio hardware or DSP dependencies.
        process = next(n for n in detector.body if isinstance(n, ast.FunctionDef)
                       and n.name == '_process_block')
        process.body = ast.parse('processed.append(time.monotonic())').body
        module = ast.fix_missing_locations(ast.Module(body=[detector], type_ignores=[]))
        processed = []
        counts = []

        def capture(queue, flag):
            while flag[0]:
                queue.append(([0.0], time.monotonic()))
                time.sleep(0.005)

        def lookup(*args):
            counts.append(len(processed))
            time.sleep(0.150)
            counts.append(len(processed))
            state['_running'] = False

        state = dict(threading=threading, time=time, collections=collections,
                     processed=processed, _running=True, _lock=threading.Lock(),
                     _timing={}, BLOCK_SIZE=512, NORM_HISTORY=300, CHUNK_SEC=4, CAPTURE_RATE=44100,
                     UPDATE_SEC=0, _get_now_playing=lambda: ('artist', 'song'),
                     _open_stream=capture, _lookup_bpm=lookup,
                     _audio_bpm=lambda *args: None)
        exec(compile(module, 'bpm_source.py', 'exec'), state)
        with patch.dict(sys.modules, librosa=types.ModuleType('librosa')):
            state['_detector_loop']()
        self.assertEqual(len(counts), 2)
        self.assertGreater(counts[1] - counts[0], 10)


if __name__ == '__main__':
    unittest.main()
