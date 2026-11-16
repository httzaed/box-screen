"""Causal transient envelopes; band labels are approximations, not source separation."""
import math
import numpy as np


class RhythmDetector:
    def __init__(self, rate=44100):
        self.rate = rate
        self.window = np.empty(0)
        self.reference = np.full(3, 0.025)
        self.flux_floor = np.zeros(3)
        self.last = np.full(3, -float('inf'))
        self.events = {name: (0.0, 0, 0.0) for name in ('kick', 'snare', 'high')}

    def process(self, mono, timestamp, sensitivity=1.0):
        n = len(mono)
        if n < 2:
            return dict(self.events)
        if n != len(self.window):
            self.window = np.hanning(n)
            freq = np.fft.rfftfreq(n, 1 / self.rate)
            self.bands = [slice(*np.searchsorted(freq, (lo, hi)))
                          for lo, hi in ((40, 220), (250, 3200), (4500, 15000))]
            self.previous = np.zeros(len(freq))

        mag = np.abs(np.fft.rfft(mono * self.window)) / (n / 2)
        # Positive changes per bin keep a new strike visible over sustained notes.
        growth = np.maximum(0.0, mag - self.previous)
        dt = n / self.rate
        sensitivity = max(0.3, min(3.0, sensitivity))
        for i, (name, band) in enumerate(zip(self.events, self.bands)):
            flux = float(np.linalg.norm(growth[band]))
            energy = float(np.linalg.norm(mag[band]))
            threshold = max(0.0015, 2.8 * self.flux_floor[i],
                            0.10 * self.reference[i]) / sensitivity
            interval = (0.070, 0.052, 0.032)[i]
            if flux > threshold and timestamp - self.last[i] >= interval:
                strength = min(1.0, math.sqrt(flux / max(0.04, self.reference[i])))
                self.last[i] = timestamp
                self.events[name] = (timestamp, self.events[name][1] + 1, strength)
            self.flux_floor[i] += (flux - self.flux_floor[i]) * (-math.expm1(-dt / 0.16))
            tau = 0.15 if energy > self.reference[i] else 1.5
            self.reference[i] += (energy - self.reference[i]) * (-math.expm1(-dt / tau))
        self.previous = mag
        return dict(self.events)


def event_level(event, now, release):
    timestamp, sequence, strength = event
    age = max(0.0, now - timestamp)
    return strength * math.exp(-age / release) if sequence and age < release * 6 else 0.0
