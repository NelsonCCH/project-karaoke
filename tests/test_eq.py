"""Tone presets: flat is untouched, every preset does what its name says, none can clip, and switching is click-free."""

import numpy as np
import pytest
from scipy import signal

from karaoke.engine import SR
from karaoke.eq import PRESETS, Equalizer, design


def sine(f, seconds=1.0, amp=1.0):
    t = np.arange(int(seconds * SR)) / SR
    y = (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return np.stack([y, y], 1)


def gain_db(name, f):
    sos, g = design(name, SR)
    if sos is None:
        return 0.0
    _, h = signal.sosfreqz(sos, worN=[f], fs=SR)
    return 20 * np.log10(abs(h[0]) * g)


def run(eq, x, block=1024):
    return np.concatenate([eq.process(x[i : i + block]) for i in range(0, len(x), block)])


def test_flat_is_untouched():
    x = np.random.default_rng(0).uniform(-1, 1, (SR, 2)).astype(np.float32)
    assert np.array_equal(run(Equalizer(SR, "Flat"), x), x)


@pytest.mark.parametrize("name", list(PRESETS))
def test_no_preset_boosts_anything(name):
    for f in np.geomspace(20, 20000, 400):
        assert gain_db(name, f) <= 0.01


def test_presets_do_what_they_say():
    rel = lambda name, lo, hi: gain_db(name, lo) - gain_db(name, hi)      # noqa: E731
    assert rel("Less bass", 50, 1000) < -5
    assert rel("More bass", 50, 1000) > 5
    assert rel("Clear", 8000, 1000) > 3
    assert rel("Warm", 8000, 1000) < -2 and rel("Warm", 150, 1000) > 2
    assert rel("Party", 60, 1000) > 5 and rel("Party", 12000, 1000) > 4
    assert gain_db("Room for your voice", 2500) - gain_db("Room for your voice", 300) < -4
    assert rel("Small speakers", 40, 1000) < -6
    assert rel("Late night", 60, 1000) < -6


def test_full_scale_bass_through_more_bass_does_not_clip():
    y = run(Equalizer(SR, "More bass"), sine(60, 2.0))
    assert np.abs(y).max() <= 1.0 + 1e-3


def test_switching_presets_has_no_click():
    x = sine(220, 3.0, 0.5)
    eq = Equalizer(SR, "Flat")
    out = []
    for n, i in enumerate(range(0, len(x), 1024)):
        if n == 60:
            eq.set("More bass")
        if n == 90:
            eq.set("Late night")
        out.append(eq.process(x[i : i + 1024]))
    y = np.concatenate(out)[:, 0]
    step = np.abs(np.diff(y))
    smooth = np.abs(np.diff(x[:, 0])).max()          # the sine's own largest sample-to-sample change
    assert step.max() < 1.1 * smooth
