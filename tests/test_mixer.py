"""Mixer stage without audio devices: vocal level, dry/shift alignment and gap-free key switching."""

import numpy as np

from karaoke.engine import SR
from karaoke.keyshift import KeyShifter
from karaoke.pipeline import MIX_BLOCK, Pipeline, SampleBuffer


def mixer_only():
    p = Pipeline.__new__(Pipeline)
    p.semitones = 0
    p.shifter = KeyShifter(SR)
    p.dry = SampleBuffer(2)
    p.wet_gain = 0.0
    return p


def run(p, x, schedule):
    """Feed x (n, 2) through _shift in mixer-sized blocks; schedule maps block index -> semitones."""
    out = []
    for b, i in enumerate(range(0, len(x), MIX_BLOCK)):
        p.semitones = schedule(b)
        out.append(p._shift(x[i : i + MIX_BLOCK]))
    return np.concatenate(out)


def test_key_zero_is_bit_exact_delayed_input():
    rng = np.random.default_rng(0)
    x = (0.2 * rng.standard_normal((SR * 3, 2))).astype(np.float32)
    y = run(mixer_only(), x, lambda b: 0)
    lag = len(x) - len(y)
    assert lag > 0
    np.testing.assert_array_equal(y, x[: len(y)])


def test_switching_key_has_no_gap_or_click():
    t = np.arange(SR * 6) / SR
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    x = np.stack([tone, tone], 1)
    switch = 2 * SR // MIX_BLOCK
    y = run(mixer_only(), x, lambda b: 3 if b >= switch else 0)
    # Output keeps pace with input (no gap) and no sample jumps far beyond the tone's own slope.
    assert len(x) - len(y) < SR * 1.5
    max_step = 2 * np.pi * 523.3 * 0.3 / SR * 1.5
    assert np.abs(np.diff(y[:, 0])).max() < max_step * 2
