"""Pause / resume / jump handling in the input stage, with a stand-in separator (no GPU, no devices)."""

import numpy as np

from karaoke.engine import SR
from karaoke.pipeline import HOLD, Pipeline, SampleBuffer


class PassSep:
    """Separator stand-in: 'vocals' = the input, no latency."""
    step = 1024
    next_start = 0

    def reset(self):
        pass

    def feed(self, x):
        return np.zeros_like(x), x


class DelaySep(PassSep):
    """Stand-in with a real separator's shape: keeps the newest `delay` samples back, emits the rest in order."""
    def __init__(self, delay=3000):
        self.delay = delay
        self.buf = np.zeros((2, 0), np.float32)

    def feed(self, x):
        self.buf = np.concatenate([self.buf, x], 1)
        n = max(0, self.buf.shape[1] - self.delay)
        out, self.buf = self.buf[:, :n], self.buf[:, n:]
        return np.zeros_like(out), out


def make(sep=None):
    p = Pipeline.__new__(Pipeline)
    p.sep = sep or PassSep()
    p.hold, p.tags, p.stems, p.out = SampleBuffer(4), SampleBuffer(2), SampleBuffer(6), SampleBuffer(2)
    p.paused = p.trim_pause = p.skip_lead = p.flush_req = p.playing = False
    p.stats = {"gpu_load": 0.0}
    return p


def tagged(x, track=1, start=0.0):
    """Tag audio like _separate_loop does: track serial and song position per sample."""
    n = len(x)
    return np.concatenate([x, np.full((n, 1), track, np.float32), (start + np.arange(n) / SR)[:, None]], 1).astype(np.float32)


def feed(p, x, block=480):
    x = x if x.shape[1] == 4 else tagged(x)
    for i in range(0, len(x), block):
        p._process_input(x[i : i + block])


def banked(p):
    return p.stems.pop(p.stems.size)[:, 2:4]


def ramp(n, start=0):
    v = (np.arange(start, start + n, dtype=np.float32) + 1) / 1e6   # never digital silence, every sample unique
    return np.stack([v, v], 1)


def test_pause_resume_joins_without_gap_or_loss():
    p = make()
    song_a, silence, song_b = ramp(SR * 2), np.zeros((SR, 2), np.float32), ramp(SR, start=SR * 2)
    feed(p, song_a)
    feed(p, silence[: SR // 5])      # Spotify already silent, pause event not here yet
    p.pause()
    feed(p, silence)                 # paused: cable keeps sending silence
    p.resume()
    feed(p, silence[: SR // 10])     # Spotify restarting
    feed(p, song_b)
    feed(p, np.full((int(HOLD * SR) + 4800, 2), 0.5, np.float32))   # push song_b out of the hold
    got = banked(p)[: len(song_a) + len(song_b)]
    np.testing.assert_array_equal(got, np.concatenate([song_a, song_b]))


def test_tags_follow_their_samples_through_pause_and_separator_delay():
    """Every banked sample keeps the song position it was captured at, even across a pause cut and a separator
    that holds audio back: that is what keeps the lyrics in sync at any delay."""
    p = make(DelaySep())
    song = ramp(SR * 3)
    x = tagged(song, track=7, start=12.0)
    feed(p, x[: SR * 2])
    feed(p, tagged(np.zeros((SR // 5, 2), np.float32), track=7, start=14.0))   # silence before the pause event
    p.pause()
    p._process_input(tagged(np.zeros((480, 2), np.float32)))
    p.resume()
    feed(p, x[SR * 2 :])
    feed(p, tagged(np.full((int(HOLD * SR) + 9600, 2), 0.5, np.float32), track=8))
    s = p.stems.pop(p.stems.size)
    audio, track, pos = s[:, 2:4], s[:, 4], s[:, 5]
    got = slice(0, len(song))
    np.testing.assert_array_equal(audio[got], song)
    np.testing.assert_array_equal(track[got], 7)
    np.testing.assert_allclose(pos[got], x[:, 3], atol=1e-4)


def test_jump_drops_everything_banked():
    p = make()
    feed(p, ramp(SR * 2))
    assert p.stems.size > 0
    p.jump()
    feed(p, ramp(SR, start=10 ** 6))
    got = banked(p)
    assert got.min() >= (10 ** 6 + 1) / 1e6      # nothing from before the jump survives
