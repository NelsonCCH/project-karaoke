"""The streaming key shift must move pitch by the requested semitones without losing audio."""

import numpy as np

from karaoke.engine import SR
from karaoke.keyshift import KeyShifter
from karaoke.pipeline import MIX_BLOCK


def dominant_freq(x):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / SR)[spec.argmax()]


def test_streaming_shift():
    t = np.arange(SR * 4) / SR
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    x = np.stack([tone, tone], 1)
    for semis in (-3, 2, 5):
        ks = KeyShifter(SR)
        ks.semitones = semis
        y = np.concatenate([ks.process(x[i : i + MIX_BLOCK]) for i in range(0, len(x), MIX_BLOCK)])
        lag = len(x) - len(y)
        f = dominant_freq(y[SR:, 0])
        expected = 440 * 2 ** (semis / 12)
        rms = np.sqrt(np.mean(y[SR:, 0] ** 2))
        print(f"{semis:+d}: {f:.1f} Hz (expected {expected:.1f}), latency {lag / SR * 1000:.0f} ms, rms {rms:.3f}")
        assert 0 <= lag < SR * 0.5
        assert abs(f - expected) < 3
        assert abs(rms - 0.3 / np.sqrt(2)) < 0.03
