"""Tone presets for the backing track: a few shelf / peak filters (RBJ Audio EQ Cookbook biquads), applied live to
the final mix. Each preset is turned down by its own peak boost (the preamp), so no preset can clip; that also
keeps "More bass" from merely sounding louder. Switching presets warms the new filter on the last half second of
audio and crossfades to it over one mixer block, so there is no click.
"""

import numpy as np
from scipy import signal

# name -> [(kind, Hz, dB, Q)]; kind is "low" / "high" shelf (Q = shelf slope, 0.7 = gentle) or "peak".
PRESETS = {
    "Flat": [],
    "Less bass": [("low", 150, -6, 0.7)],
    "More bass": [("low", 100, 6, 0.7)],
    "Clear": [("low", 120, -2, 0.7), ("high", 4000, 4, 0.7)],
    "Warm": [("low", 250, 3, 0.7), ("high", 6000, -3, 0.7)],
    "Party": [("low", 90, 5, 0.7), ("peak", 1000, -2, 0.7), ("high", 8000, 4, 0.7)],
    "Room for your voice": [("peak", 2500, -5, 0.9)],      # dips where a singer's voice sits, so yours cuts through
    "Small speakers": [("low", 70, -8, 0.7), ("peak", 180, 3, 1.0)],   # no deep bass they cannot play anyway
    "Late night": [("low", 150, -8, 0.7), ("high", 6000, -3, 0.7)],
}
DEFAULT_EQ = "Flat"
WARM = 0.5          # seconds of recent input a new filter runs over before it is heard


def _biquad(kind, f, db, q, sr):
    """One RBJ biquad as a normalised SOS row [b0 b1 b2 1 a1 a2]."""
    a = 10 ** (db / 40)
    w = 2 * np.pi * f / sr
    cw, sw = np.cos(w), np.sin(w)
    if kind == "peak":
        al = sw / (2 * q)
        b = [1 + al * a, -2 * cw, 1 - al * a]
        d = [1 + al / a, -2 * cw, 1 - al / a]
    else:
        al = sw / 2 * np.sqrt((a + 1 / a) * (1 / q - 1) + 2)
        r = 2 * np.sqrt(a) * al
        if kind == "low":
            b = [a * ((a + 1) - (a - 1) * cw + r), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - r)]
            d = [(a + 1) + (a - 1) * cw + r, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - r]
        else:
            b = [a * ((a + 1) + (a - 1) * cw + r), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - r)]
            d = [(a + 1) - (a - 1) * cw + r, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - r]
    return np.array(b + d) / d[0]


def design(name, sr):
    """(sos or None for flat, preamp gain) of a preset."""
    bands = PRESETS[name]
    if not bands:
        return None, 1.0
    sos = np.array([_biquad(*band, sr) for band in bands])
    _, h = signal.sosfreqz(sos, worN=np.geomspace(10, sr / 2 * 0.99, 8000), fs=sr)
    return sos, min(1.0, 1 / np.abs(h).max())


class Equalizer:
    def __init__(self, sr, name=DEFAULT_EQ):
        self.sr = sr
        self.hist = np.zeros((0, 2), np.float32)        # recent input, to warm a new filter
        self.name, self.sos, self.gain, self.zi = name, *design(name, sr), None
        self.zi = self._fresh_state(self.sos)
        self.next = None                                # preset waiting to be faded in

    def _fresh_state(self, sos):
        return None if sos is None else np.zeros((len(sos), 2, 2))

    def set(self, name):
        if name != self.name:
            self.next = name

    def reset(self):
        """Forget the audio heard so far (a skip or seek): the filters start from silence."""
        self.hist = self.hist[:0]
        self.zi = self._fresh_state(self.sos)

    @staticmethod
    def _run(sos, gain, zi, x):
        if sos is None:
            return x.copy(), zi
        y, zi = signal.sosfilt(sos, x, axis=0, zi=zi)
        return (y * gain).astype(np.float32), zi

    def process(self, x):
        """(n, 2) float32 -> (n, 2) float32."""
        if len(x) == 0:
            return x
        y, self.zi = self._run(self.sos, self.gain, self.zi, x)
        if self.next is not None:
            name, self.next = self.next, None
            sos, gain = design(name, self.sr)
            zi = self._fresh_state(sos)
            if sos is not None and len(self.hist):
                _, zi = self._run(sos, gain, zi, self.hist)
            y2, zi = self._run(sos, gain, zi, x)
            f = np.linspace(0.0, 1.0, len(x), dtype=np.float32)[:, None]
            y = y * (1 - f) + y2 * f
            self.name, self.sos, self.gain, self.zi = name, sos, gain, zi
        self.hist = np.concatenate([self.hist, x])[-int(WARM * self.sr):]
        return y
