"""The Mac player's mic queue (mac/player.py), without a Mac: bursts and two clocks must not break up the voice."""

import importlib.util
import os

import numpy as np

spec = importlib.util.spec_from_file_location("player", os.path.join(os.path.dirname(__file__), "..", "mac", "player.py"))
player = importlib.util.module_from_spec(spec)
spec.loader.exec_module(player)
BLOCK, SR = player.BLOCK, player.SR


def run(events):
    """events: (time, "in" | "out"). Returns the player and the times a started voice came up short."""
    p = player.Player.__new__(player.Player)
    p.lock = __import__("threading").Lock()
    p.mic_q, p.mic_size, p.mic_peak = __import__("collections").deque(), 0, 0.0
    p.mic_margin, p.mic_primed, p.mic_gaps, p.block = 0, False, 0, BLOCK
    p.mic_times = p.out_times = p.mic_fill = __import__("collections").deque(maxlen=10)
    shorts, p.after_take = [], []
    for when, kind in sorted(events):
        if kind == "in":
            p.mic_callback(np.ones((BLOCK, 1), np.float32), BLOCK, None, None)
        else:
            was = p.mic_primed
            p.take_mic(BLOCK)
            p.after_take.append(p.mic_size)
            if was and not p.mic_primed:
                shorts.append(when)
    return p, shorts


def test_bursty_devices_settle_without_gaps():
    """CoreAudio hands each device's 512-frame buffer over as two 256-frame callbacks back to back; the mic and the
    speakers do so on unrelated phases. After learning its margin the voice never comes up short."""
    period = 512 / SR
    ev = []
    for i in range(3000):
        ev += [(i * period, "in"), (i * period + 1e-5, "in")]
        ev += [(i * period + 0.37 * period, "out"), (i * period + 0.37 * period + 1e-5, "out")]
    p, shorts = run(ev)
    late = [s for s in shorts if s > 1.0]
    assert late == [], late
    assert p.mic_margin <= player.MIC_MARGIN_MAX * SR


def test_mic_clock_running_ahead_does_not_build_delay():
    ev = [(i * BLOCK / SR / 1.01, "in") for i in range(20000)] + [(i * BLOCK / SR, "out") for i in range(19800)]
    p, _ = run(ev)
    assert max(p.after_take[len(p.after_take) // 2 :]) <= 2 * BLOCK + p.mic_margin   # never more than its target
