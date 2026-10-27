"""The PC's vocal chain (karaoke/mic.py) on synthetic signals: no device needed."""

import threading
from collections import deque

import numpy as np

from karaoke.mic import CHUNK, FB_HOLD, FB_MAX_DB, FB_RELEASE_DB, LIMIT, SIZES, VOICES, Mic, VocalChain, level_gain

SR = 48000


def tone(seconds, f=220.0, amp=0.1):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def run(chain, x, block=480):
    return np.concatenate([chain.process(x[i : i + block]) for i in range(0, len(x), block)])


def dry(chain):
    chain.reverb_mix = chain.echo_mix = 0.0
    chain.gate_on = chain.comp_on = chain.fb_on = False     # a steady test tone is exactly what anti-feedback cuts
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
    m.q, m.size, m.max, m.lock = deque(), 0, 4800, threading.Lock()
    m.block, m.margin, m.margin_max, m.primed = 0, 0, 960, False
    return m


def test_mixed_block_sizes_never_leave_a_gap():
    """Mic blocks of 480 every 10 ms, speaker blocks of 240 every 5 ms, with the two callbacks jittering against
    each other by up to 3 ms, as measured: once the voice has started, no speaker block may come up short."""
    m = queue_only()
    rng = np.random.default_rng(2)
    events = [(i * 0.010 + rng.uniform(0, 0.003), "in") for i in range(600)]
    events += [(i * 0.005 + rng.uniform(0, 0.003), "out") for i in range(1200)]
    started, short = False, 0
    for _, kind in sorted(events):
        if kind == "in":
            m._in_cb(np.ones((480, 1), np.float32), 480, None, None)
        else:
            was = m.primed
            m.pull(240)
            started |= m.primed
            short += started and was and not m.primed
    assert started and short == 0


def test_a_late_mic_teaches_the_queue_its_margin():
    """As measured on the ATR2100x: now and then a 10 ms block comes 21 ms after the last one (and the next on
    time). After a few such gaps the margin covers it and there are no more."""
    m = queue_only()
    t, events, rng = 15.0, [], np.random.default_rng(3)
    for i in range(1500):
        events.append((i * 0.010 + (0.011 if rng.random() < 0.02 else 0.0), "in"))
    events += [(i * 0.005, "out") for i in range(int(t / 0.005))]
    shorts = []
    for when, kind in sorted(events):
        if kind == "in":
            m._in_cb(np.ones((480, 1), np.float32), 480, None, None)
        else:
            was = m.primed
            m.pull(240)
            if was and not m.primed:
                shorts.append(when)
    assert len(shorts) <= 3 and all(s < t / 2 for s in shorts)
    assert m.margin <= m.margin_max


def test_mic_clock_running_ahead_does_not_build_delay():
    m = queue_only()
    for i in range(2000):                   # the mic delivers 1% more than the speakers take
        m._in_cb(np.ones((485, 1), np.float32), 485, None, None)
        m.pull(240)
        m.pull(240)
    assert m.size <= 485 + 240 + 485


def test_queue_keeps_the_voice_live():
    m = queue_only()
    for k in range(10):                     # the mic ran ahead of the speakers by 10 blocks
        m._in_cb(np.full((480, 1), k, np.float32), 480, None, None)
    assert m.size <= m.max
    x = np.full(480, 1.0, np.float32)
    assert m.pull(4000).shape == (4000,)    # more than queued: the rest is silence, never an error


# ---- anti-feedback ---------------------------------------------------------------------------------------------------

def fb_only():
    c = dry(VocalChain(SR))
    c.level, c.fb_on = 1.0, True
    return c


def sung_note(seconds, f0=220.0, amp=0.1, vibrato=True):
    """A voice-like note: ten partials falling off as 1/k, with or without a 5.5 Hz, +-30 cent vibrato."""
    t = np.arange(int(seconds * SR)) / SR
    f = f0 * 2 ** ((0.3 / 12) * np.sin(2 * np.pi * 5.5 * t)) if vibrato else np.full_like(t, f0)
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = sum(np.sin(k * ph) / k for k in range(1, 11))
    return (amp * x / np.abs(x).max()).astype(np.float32)


def howling_room(chain, seconds, delay=0.036, suppress=True):
    """Closed loop: whatever the chain plays comes back into the mic `delay` s later through a room that rings at
    2.3 kHz with a loop gain of ~1.2 (it howls on its own) and 0.3 elsewhere. A short shout starts it."""
    from scipy import signal as sg
    from karaoke.eq import _biquad
    chain.fb_on = suppress
    room = np.array([_biquad("peak", 2300, 12, 8, SR)])
    zi = np.zeros((1, 2))
    d = int(delay * SR)
    out = np.zeros(int(seconds * SR) + d, np.float32)
    rng = np.random.default_rng(0)
    for i in range(0, int(seconds * SR), CHUNK):
        back, zi = sg.sosfilt(room, 0.3 * out[i : i + CHUNK], zi=zi)        # out[i] was played d samples earlier
        voice = rng.normal(0, 0.1 if i < 0.2 * SR else 1e-4, CHUNK)
        out[i + d : i + d + CHUNK] = chain.process((voice + back).astype(np.float32))
    return out[d:]


def test_feedback_howls_without_and_dies_with_the_suppressor():
    y_off = howling_room(fb_only(), 4, suppress=False)
    y_on = howling_room(fb_only(), 4)
    assert db(y_off[3 * SR:]) > -12                       # the room really howls (held at the limiter)
    assert db(y_on[3 * SR:]) < -40                        # ...and the notch stops it


def test_feedback_is_caught_within_half_a_second():
    c = fb_only()
    y = howling_room(c, 4)
    assert db(y[int(0.7 * SR) : SR]) < -30
    assert any(abs(f - 2300) < 60 for f, depth, _ in c.fb.notches)


def test_sung_notes_are_never_notched():
    for vib in (True, False):
        for f0 in (110, 220, 440, 880):
            c = fb_only()
            run(c, sung_note(3, f0, amp=0.3, vibrato=vib))
            assert c.fb.notches == [], (f0, vib, c.fb.notches)


def test_notches_let_go_after_the_howl_stops():
    c = fb_only()
    run(c, tone(1.5, 1500, 0.2))
    assert c.fb.notches
    run(c, sung_note(FB_HOLD + 2, 220, 0.1))
    assert c.fb.notches                                   # held for a while: the howl may come back
    run(c, sung_note(FB_MAX_DB / FB_RELEASE_DB + 1, 220, 0.1))
    assert c.fb.notches == []
