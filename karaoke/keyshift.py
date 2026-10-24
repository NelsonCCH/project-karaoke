"""Streaming key shift on Rubber Band's R3 ("finer") engine in real-time mode.

pedalboard's streaming PitchShift returned silence when fed block by block, so this talks to librubberband
directly through pylibrb. Output arrives in variable-sized blocks once the stretcher's latency is filled.
"""

import numpy as np
import pylibrb
from pylibrb import Option


class KeyShifter:
    def __init__(self, sr, channels=2):
        opts = Option.PROCESS_REALTIME | Option.ENGINE_FINER | Option.PitchHighConsistency | Option.CHANNELS_TOGETHER
        self.rb = pylibrb.RubberBandStretcher(sr, channels, opts)
        self.ch = channels
        self._semitones = 0
        # Prime with the recommended padding and discard the matching start delay, as the library docs advise.
        self.rb.process(np.zeros((channels, self.rb.get_preferred_start_pad()), np.float32))
        self.to_drop = self.rb.get_start_delay()

    @property
    def semitones(self):
        return self._semitones

    @semitones.setter
    def semitones(self, s):
        if s != self._semitones:
            self._semitones = s
            self.rb.pitch_scale = 2 ** (s / 12)

    def process(self, x):
        """x: (n, ch) float32 -> (m, ch) float32."""
        self.rb.process(np.ascontiguousarray(x.T, dtype=np.float32))
        n = self.rb.available()
        if n <= 0:
            return np.zeros((0, self.ch), np.float32)
        y = self.rb.retrieve(n)
        if self.to_drop:
            d = min(self.to_drop, y.shape[1])
            y = y[:, d:]
            self.to_drop -= d
        return np.ascontiguousarray(y.T, dtype=np.float32)
