"""Détection des coups (grosse caisse, caisse claire, charleys) pour le mode Rythme des LEDs.

Les bandes sont des approximations : ce n'est pas une vraie séparation des instruments.

Version 2 :
  - Analyse sur une fenêtre de 2048 échantillons (~46 ms), glissante tous les blocs de 512.
    Avec 512 seuls, une basse à 50 Hz ne tenait même pas une période dans la fenêtre :
    son énergie sautait d'un bloc à l'autre et déclenchait de faux coups.
  - On repère l'attaque (« flux » : combien le spectre monte d'un bloc à l'autre, en échelle log)
    au lieu de la simple montée d'énergie. Une note tenue ne monte qu'une fois.
  - Caisse claire : il faut une montée sur presque toute la bande 1,5-5 kHz (bruit large),
    ce qui écarte la voix et les synthés dont seules quelques harmoniques bougent.
"""
import math
import numpy as np

WINDOW = 2048

# name: (bande Hz, écart minimal entre deux coups en s)
_BANDS = {
    'kick': ((40, 150), 0.09),
    'snare': ((1500, 5000), 0.09),
    'high': ((7000, 14000), 0.06),
}
_NAMES = tuple(_BANDS)
_SNARE_SPREAD = 0.55    # part des fréquences de la bande qui doivent monter ensemble
_K = 2.6                # le flux doit dépasser ~2,6 fois son niveau habituel
_MASK = 0.8             # après un coup : 80 % de son flux pour redéclencher…
_MASK_TAU = 0.2         # …exigence qui retombe en ~200 ms


class RhythmDetector:
    def __init__(self, rate=44100):
        self.rate = rate
        self.buffer = np.zeros(WINDOW)
        self.window = np.hanning(WINDOW)
        freq = np.fft.rfftfreq(WINDOW, 1 / rate)
        self.masks = [(freq >= lo) & (freq < hi) for (lo, hi), _ in _BANDS.values()]
        self.prev_log = None
        self.flux_ref = np.full(3, 0.02)
        self.energy_ref = np.full(3, 0.01)
        self.last = np.full(3, -float('inf'))
        self.masking = np.zeros(3)   # flux du dernier coup, qui retombe : masque ses rebonds
        self.events = {name: (0.0, 0, 0.0) for name in _NAMES}
        # compatibilité avec l'ancienne interface
        self.previous = np.zeros(3)
        self.reference = self.energy_ref

    def process(self, mono, timestamp, sensitivity=1.0):
        mono = np.asarray(mono, dtype=float)
        n = len(mono)
        if n == 0:
            return dict(self.events)
        if n >= WINDOW:
            self.buffer = mono[-WINDOW:].copy()
        else:
            self.buffer = np.concatenate([self.buffer[n:], mono])
        mag = np.abs(np.fft.rfft(self.buffer * self.window)) / (WINDOW / 4)
        log = np.log1p(200.0 * mag)
        dt = n / self.rate
        if self.prev_log is None:
            self.prev_log = log
            return dict(self.events)
        diff = log - self.prev_log
        self.prev_log = log
        sens = max(0.3, float(sensitivity))
        for i, name in enumerate(_NAMES):
            mask = self.masks[i]
            band_diff = diff[mask]
            flux = float(np.mean(np.maximum(0.0, band_diff)))
            energy = float(np.linalg.norm(mag[mask]))
            # Juste après un coup, la traîne (ou une basse qui bat avec la grosse caisse) remonte
            # un peu : on exige alors un flux proche de celui du coup lui-même.
            threshold = max(0.03, self.flux_ref[i] * _K, self.masking[i] * _MASK) / sens
            self.masking[i] = max(self.masking[i] * math.exp(-dt / _MASK_TAU), flux)
            ok = flux > threshold and energy > 0.002
            if ok and name == 'snare':
                ok = float(np.mean(band_diff > 0.05)) >= _SNARE_SPREAD
            if ok and timestamp - self.last[i] >= _BANDS[name][1]:
                strength = min(1.0, energy / max(0.06, self.energy_ref[i] * 1.6))
                self.last[i] = timestamp
                self.events[name] = (timestamp, self.events[name][1] + 1, strength)
            # Niveaux habituels : le flux de référence suit lentement (les coups le font peu bouger).
            self.flux_ref[i] += (min(flux, self.flux_ref[i] * 3) - self.flux_ref[i]) * (-math.expm1(-dt / 1.5))
            tau = 0.3 if energy > self.energy_ref[i] else 3.0
            self.energy_ref[i] += (energy - self.energy_ref[i]) * (-math.expm1(-dt / tau))
        return dict(self.events)


def event_level(event, now, release):
    timestamp, sequence, strength = event
    age = max(0.0, now - timestamp)
    return strength * math.exp(-age / release) if sequence and age < release * 6 else 0.0


def beat_mix(levels, previous_mix=0.0, floor=0.0):
    """Mode Rythme : kick -> couleur 1, caisse claire -> couleur 2, charleys -> scintillement.

    Retourne (intensite 0-1, mix 0-1 entre couleur 1 et couleur 2).
    Le mix garde sa derniere valeur quand seuls les charleys jouent,
    pour eviter un saut de couleur parasite.
    """
    kick = min(1.0, max(0.0, levels.get('kick', 0.0)))
    snare = min(1.0, max(0.0, levels.get('snare', 0.0)))
    high = min(1.0, max(0.0, levels.get('high', 0.0)))
    hit = max(kick, 0.9 * snare)
    sparkle = 0.3 * high ** 1.5 * (1.0 - hit)
    intensity = min(1.0, hit + sparkle)
    weight = kick + snare
    mix = snare / weight if weight > 0.05 else previous_mix
    floor = min(1.0, max(0.0, floor))
    return floor + (1.0 - floor) * intensity, mix
