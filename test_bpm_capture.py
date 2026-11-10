import unittest
import numpy as np
from bpm_source import _CaptureResampler, BLOCK_SIZE, CAPTURE_RATE


class CaptureResamplerTests(unittest.TestCase):
    def test_continuity_and_fixed_blocks(self):
        for rate in (44100, 48000, 96000, 22050):
            with self.subTest(rate=rate):
                samples = np.sin(2 * np.pi * 80 * np.arange(rate) / rate).astype(np.float32)
                resampler = _CaptureResampler(rate)
                blocks = []
                for start in range(0, len(samples), 512):
                    blocks.extend(resampler.push(samples[start:start + 512]))
                self.assertTrue(all(len(block) == BLOCK_SIZE for block in blocks))
                actual = np.concatenate(blocks)
                positions = np.arange(len(actual)) * rate / CAPTURE_RATE
                expected = np.interp(positions, np.arange(rate), samples)
                np.testing.assert_allclose(actual, expected, atol=1e-6)
                self.assertLess(CAPTURE_RATE - len(actual), BLOCK_SIZE + 2)
                self.assertLess(len(resampler.pending), 2 * BLOCK_SIZE + 2)


if __name__ == '__main__':
    unittest.main()
