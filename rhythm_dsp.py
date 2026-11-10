"""Causal transient envelopes; band labels are approximations, not source separation."""
import math
import numpy as np


class RhythmDetector:
    def __init__(self, rate=44100):
        self.rate = rate
        self.previous = np.zeros(3)
        self.reference = np.full(3, 0.025)
        self.last = np.full(3, -float('inf'))
        self.events = {name: (0.0, 0, 0.0) for name in ('kick', 'snare', 'high')}

    def process(self, mono, timestamp, sensitivity=1.0):
        n = len(mono)
        mag = np.abs(np.fft.rfft(mono * np.hanning(n))) / (n / 2)
        freq = np.fft.rfftfreq(n, 1 / self.rate)
        energy = np.array([np.linalg.norm(mag[(freq >= lo) & (freq < hi)])
                           for lo, hi in ((40, 220), (250, 2000), (2000, 10000))])
        dt = n / self.rate
        rise = np.maximum(0, energy - self.previous)
        for i, name in enumerate(self.events):
            # Absolute floor prevents normalizing silence/noise to full brightness.
            threshold = max(0.002, self.reference[i] * 0.18) / sensitivity
            if (rise[i] > threshold and rise[i] > energy[i] * 0.22
                    and timestamp - self.last[i] >= (0.075 if i == 0 else 0.055)):
                strength = min(1.0, float(energy[i] / max(0.025, self.reference[i] * 1.4)))
                self.last[i] = timestamp
                self.events[name] = (timestamp, self.events[name][1] + 1, strength)
            tau = 0.15 if energy[i] > self.reference[i] else 4.0
            self.reference[i] += (energy[i] - self.reference[i]) * (-math.expm1(-dt / tau))
        self.previous = energy
        return dict(self.events)


def event_level(event, now, release):
    timestamp, sequence, strength = event
    age = max(0.0, now - timestamp)
    return strength * math.exp(-age / release) if sequence and age < release * 6 else 0.0
