"""After a skip / seek, Spotify's report arrives before its audio changes: the old song runs on for 0.10-0.19 s,
then (on a restart) ~0.2 s of near silence, then the new audio. Measured on the cable 2026-10-24; these shapes
are those measurements. The old tail must not be played, and nothing of the new audio may be lost."""

import numpy as np

from karaoke.engine import SR
from karaoke.pipeline import Pipeline

import test_transport as T


def tone(seconds, f, amp=0.1):
    t = np.arange(int(seconds * SR)) / SR
    y = (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return np.stack([y, y], 1)


def noise_floor(seconds):
    return (np.random.default_rng(1).standard_normal((int(seconds * SR), 2)) * 3e-4).astype(np.float32)


OLD, NEW = 330.0, 520.0


def starts_with_new(y):
    """y begins with the new tone (no old-tone samples at its start)."""
    head = y[:441, 0]
    t = np.arange(len(head)) / SR
    old = abs(np.dot(head, np.sin(2 * np.pi * OLD * t)) + 1j * np.dot(head, np.cos(2 * np.pi * OLD * t)))
    new = abs(np.dot(head, np.sin(2 * np.pi * NEW * t)) + 1j * np.dot(head, np.cos(2 * np.pi * NEW * t)))
    return new > 5 * old


def test_restart_drops_old_tail_and_gap():
    x = np.concatenate([tone(0.15, OLD), noise_floor(0.2), tone(0.4, NEW)])
    y = Pipeline._cut_seek_tail(x)
    assert starts_with_new(y)
    assert abs(len(y) - int(0.4 * SR)) < SR // 100        # all of the new audio, to within one 10 ms frame


def test_mid_song_seek_cuts_at_the_dip():
    dip = tone(0.02, OLD, amp=0.002)
    x = np.concatenate([tone(0.13, OLD), dip, tone(0.5, NEW)])
    y = Pipeline._cut_seek_tail(x)
    assert len(y) >= int(0.5 * SR) and len(y) <= int(0.52 * SR) + SR // 100
    assert not starts_with_new(x) and np.abs(y[-int(0.5 * SR):] - tone(0.5, NEW)).max() == 0


def test_already_new_audio_is_kept_whole():
    x = tone(0.5, NEW)
    assert len(Pipeline._cut_seek_tail(x)) == len(x)


def test_jump_through_the_pipeline_plays_no_old_audio():
    p = T.make()
    T.feed(p, tone(3, OLD))
    p.jump()
    T.feed(p, np.concatenate([tone(0.15, OLD), noise_floor(0.2), tone(3, NEW)]))
    y = p.stems.pop(p.stems.size)[:, 2:4]      # PassSep: the "vocal" stem is the input itself
    loud = np.flatnonzero(np.abs(y[:, 0]) > 0.01)
    assert starts_with_new(y[loud[0]:])
