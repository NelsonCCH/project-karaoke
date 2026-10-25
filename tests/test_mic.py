"""The PC's vocal chain (karaoke/mic.py) on synthetic signals: no device needed."""

import threading
from collections import deque

import numpy as np

from karaoke.mic import CHUNK, LIMIT, SIZES, VOICES, Mic, VocalChain, level_gain

SR = 48000


def tone(seconds, f=220.0, amp=0.1):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def run(chain, x, block=480):
    return np.concatenate([chain.process(x[i : i + block]) for i in range(0, len(x), block)])


def dry(chain):
    chain.reverb_mix = chain.echo_mix = 0.0
    chain.gate_on = chain.comp_on = False
    return chain


def db(x):
    return 10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + 1e-12)


def test_level_curve():
    assert level_gain(0) == 0 and abs(level_gain(50) - 1) < 1e-9
    assert abs(20 * np.log10(level_gain(100)) - 18) < 1e-6


def test_muted_mic_is_silent():
    c = VocalChain(SR)
    assert np.abs(run(c, tone(1))).max() == 0


def test_unity_passes_the_voice_unchanged_in_level():
    c = dry(VocalChain(SR))
    c.level = 1.0
    y = run(c, tone(2, 440))
    assert abs(db(y[SR:]) - db(tone(1, 440))) < 0.3          # the 90 Hz high-pass leaves 440 Hz alone


def test_high_pass_removes_rumble():
    c = dry(VocalChain(SR))
    c.level = 1.0
    y = run(c, tone(2, 30))
    assert db(y[SR:]) < db(tone(1, 30)) - 12


def test_limiter_holds_the_ceiling_for_any_shout():
    c = VocalChain(SR)
    c.level, c.reverb_mix, c.echo_mix = level_gain(100), 0.6, 0.7
    y = run(c, tone(3, 300, amp=0.99))
    assert np.abs(y).max() <= LIMIT + 1e-6


def test_echo_repeats_after_its_time():
    c = dry(VocalChain(SR))
    c.level, c.echo_mix = 1.0, 1.0
    c.echo.ms, c.echo.repeat = 200, 0.0
    x = np.zeros(SR, np.float32)
    x[1000:1100] = 0.5
    y = run(c, x)
    late = np.abs(y[3000:]).argmax() + 3000        # past the direct sound and the high-pass's own ringing
    assert abs(late - (1000 + int(0.2 * SR))) < 200


def test_bigger_reverb_rings_longer():
    tails = {}
    for name in SIZES:
        c = dry(VocalChain(SR))
        c.level, c.reverb_mix = 1.0, 1.0
        c.reverb.size(name)
        y = run(c, np.concatenate([tone(0.3, 500, 0.3), np.zeros(2 * SR, np.float32)]))
        tails[name] = db(y[int(1.3 * SR):])
    assert tails["Small room"] < tails["Room"] < tails["Hall"]


def test_gate_quiets_noise_between_phrases():
    rng = np.random.default_rng(0)
    hiss = (rng.standard_normal(2 * SR) * 10 ** (-60 / 20)).astype(np.float32)
    on, off = dry(VocalChain(SR)), dry(VocalChain(SR))
    on.level = off.level = 1.0
    on.gate_on = True
    assert db(run(on, hiss)[SR:]) < db(run(off, hiss)[SR:]) - 12
    sung = tone(1, 300, 0.1)
    assert abs(db(run(on, sung)[SR // 2:]) - db(run(off, sung)[SR // 2:])) < 0.5    # singing passes untouched


def test_compressor_narrows_loud_and_soft():
    c = dry(VocalChain(SR))
    c.level, c.comp_on = 1.0, True
    loud, soft = run(c, tone(1, 300, 0.5))[SR // 2:], run(c, tone(1, 300, 0.02))[SR // 2:]
    assert db(loud) - db(soft) < 20 * np.log10(0.5 / 0.02) - 8


def test_every_voice_preset_is_sane():
    for name in VOICES:
        c = dry(VocalChain(SR))
        c.level = 1.0
        c.set_voice(name)
        y = run(c, tone(1, 1000, 0.1))
        assert np.isfinite(y).all() and abs(db(y[SR // 2:]) - db(tone(0.5, 1000, 0.1))) < 6


def test_odd_callback_sizes_make_no_clicks():
    c = VocalChain(SR)
    c.level = 1.0
    x = tone(2, 220, 0.2)
    rng = np.random.default_rng(1)
    out, i = [], 0
    while i < len(x):
        n = int(rng.integers(1, 3 * CHUNK))
        out.append(c.process(x[i : i + n]))
        i += n
    y = np.concatenate(out)
    assert len(y) == len(x) and np.abs(np.diff(y)).max() < 0.05


def queue_only():
    m = Mic.__new__(Mic)
    m.chain = dry(VocalChain(SR))
    m.chain.level = 1.0
    m.q, m.size, m.max, m.lock = deque(), 0, 960, threading.Lock()
    return m


def test_queue_keeps_the_voice_live():
    m = queue_only()
    for k in range(10):                     # the mic ran ahead of the speakers by 10 blocks
        m._in_cb(np.full((480, 1), k, np.float32), 480, None, None)
    assert m.size <= m.max
    x = np.full(480, 1.0, np.float32)
    assert m.pull(4000).shape == (4000,)    # more than queued: the rest is silence, never an error
