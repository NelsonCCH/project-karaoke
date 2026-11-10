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
# Room correction (room_cuts): cuts where the room makes the speakers boom at the listening spot
ROOM_LO, ROOM_HI = 100, 500     # Hz searched: bass notes, a low voice's fundamentals, the low-mids where rooms boom
ROOM_MIN_DB = 3                 # a bulge smaller than this is left alone
ROOM_MAX_DB = 9                 # no cut deeper than this
ROOM_Q = 4                      # ~1/3 octave: as wide as a room's bass bulges, narrower than a tone preset
ROOM_CUTS = 4


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


def room_cuts(f, mag, sr=48000):
    """Peaking cuts [("peak", Hz, -dB, Q)] that flatten the bulges of a measured speakers -> mic response.

    f, mag: frequencies and linear magnitude (tools/loop_check.py measures them, the mic where people listen,
    pointing at the speakers). Measured in the development room: +7.4 dB at 125-131 Hz and +7.7 at 175-185
    over the 100-1000 Hz median. A song's bass sitting there (one intro at 138-175 Hz) hummed through the room, and
    low sung notes boomed the same way. The response is smoothed over 1/6 octave (a room's bass, not its every comb
    line), then the worst bulge is cut, the next found on what is left, and so on: never a boost, never deeper
    than ROOM_MAX_DB."""
    grid = ROOM_LO / 1.25 * 2 ** (np.arange(0, 64) / 24)
    grid = grid[grid <= 1000]
    p = np.cumsum(np.asarray(mag, np.float64) ** 2)
    lo = np.searchsorted(f, grid / 2 ** (1 / 12))
    hi = np.maximum(np.searchsorted(f, grid * 2 ** (1 / 12)), lo + 1)
    db = 10 * np.log10((p[hi] - p[lo]) / (hi - lo) + 1e-30)
    db -= np.median(db[grid >= ROOM_LO])
    search = (grid >= ROOM_LO) & (grid <= ROOM_HI)
    cuts = []
    for _ in range(ROOM_CUTS):
        left = db.copy()
        if cuts:
            _, h = signal.sosfreqz(np.array([_biquad(*c, sr) for c in cuts]), worN=2 * np.pi * grid / sr)
            left += 20 * np.log10(np.abs(h))
        i = int(np.argmax(np.where(search, left, -np.inf)))
        if left[i] < ROOM_MIN_DB:
            break
        cuts.append(("peak", round(float(grid[i]), 1), -round(min(float(left[i]), ROOM_MAX_DB), 1), ROOM_Q))
    return cuts


def design(name, sr, room=()):
    """(sos or None for flat, preamp gain) of a preset, plus the room correction's cuts."""
    bands = PRESETS[name] + [tuple(c) for c in room]
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
        self.room = []                                  # room correction cuts (room_cuts), on top of any preset

    def set_room(self, cuts):
        cuts = [tuple(c) for c in cuts]
        if cuts != self.room:
            self.room = cuts
            self.next = self.name                       # faded in like a preset change

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
            sos, gain = design(name, self.sr, self.room)
            zi = self._fresh_state(sos)
            if sos is not None and len(self.hist):
                _, zi = self._run(sos, gain, zi, self.hist)
            y2, zi = self._run(sos, gain, zi, x)
            f = np.linspace(0.0, 1.0, len(x), dtype=np.float32)[:, None]
            y = y * (1 - f) + y2 * f
            self.name, self.sos, self.gain, self.zi = name, sos, gain, zi
        self.hist = np.concatenate([self.hist, x])[-int(WARM * self.sr):]
        return y
