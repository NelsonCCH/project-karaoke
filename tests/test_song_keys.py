"""The key and the vocal are remembered per song: a song comes back at the key and vocal it was last sung with, a new
song starts at 0 and the default vocal, and the switch happens when the new song is heard, not when Spotify starts
it seconds earlier."""

import threading
import time

import numpy as np

from karaoke.engine import SR
from karaoke.eq import DEFAULT_EQ, Equalizer
from karaoke.keyshift import KeyShifter
from karaoke.pipeline import Pipeline, SampleBuffer
from karaoke.session import DEFAULT_VOCAL, Session

import test_transport as T

SONGS = {1: {"title": "First Song", "artist": "Some Artist"}, 2: {"title": "Second Song", "artist": "Some Artist"},
         3: {"title": "first song ", "artist": "Some Artist"}}


class Watcher:
    serial = 1
    tracks = SONGS


class Pipe:
    heard_serial = None

    def heard(self):
        return None if self.heard_serial is None else (self.heard_serial, 12.0)


def session():
    s = Session.__new__(Session)
    s.settings = {"key": 0, "vocal": DEFAULT_VOCAL}
    s.song_keys, s.song_vocals = {}, {}
    s.watcher = Watcher()
    s.pipe = Pipe()
    return s


def test_a_song_comes_back_at_its_key_and_new_songs_start_at_zero():
    s = session()
    s.pipe.heard_serial = 1
    s._remember("key", -1)
    assert s._on_track(2) == (0, DEFAULT_VOCAL / 100) and s.settings["key"] == 0
    assert s._on_track(1)[0] == -1 and s.settings["key"] == -1
    assert s._on_track(3)[0] == -1          # the same song played again from another list: same title and artist


def test_back_to_zero_forgets_the_song():
    s = session()
    s.pipe.heard_serial = 1
    s._remember("key", 2)
    s._remember("key", 0)
    s._remember("vocal", 40)
    s._remember("vocal", DEFAULT_VOCAL)
    assert s.song_keys == {} and s.song_vocals == {}


def test_a_key_set_while_the_next_song_buffers_is_kept_for_it():
    s = session()
    s.pipe.heard_serial = None              # after a skip nothing is heard yet; Spotify is already on song 2
    s.watcher.serial = 2
    s._remember("key", 3)
    assert s._on_track(2)[0] == 3


def test_a_song_comes_back_at_its_vocal_and_new_songs_start_at_the_default():
    s = session()
    s.pipe.heard_serial = 1
    s._remember("vocal", 40)
    assert s._on_track(2) == (0, DEFAULT_VOCAL / 100) and s.settings["vocal"] == DEFAULT_VOCAL
    assert s._on_track(1) == (0, 0.4) and s.settings["vocal"] == 40
    assert s._on_track(3)[1] == 0.4


def mixer(on_track):
    p = T.make()
    p.on_track, p.mix_track = on_track, None
    p.running, p.mix_reset, p.playing, p.prime = True, False, True, 0
    p.sink, p.ahead, p.out_sr, p.out_latency, p.last_drop = None, 1e9, SR, 0.0, 0.0
    p.stats = {"dropped": 0}
    p.vocal_level, p.volume, p.semitones, p.eq_preset = 1.0, 1.0, 0, DEFAULT_EQ
    p.eq, p.shifter, p.dry, p.wet_gain = Equalizer(SR, DEFAULT_EQ), KeyShifter(SR), SampleBuffer(2), 0.0
    p.rs_out = type("Same", (), {"resample_chunk": staticmethod(lambda y: y)})()
    return p


def test_the_mixer_switches_key_with_the_first_sample_of_the_new_song():
    seen = []

    def on_track(serial):
        seen.append((serial, p.stems.size))
        return {1: (-1, 0.1), 2: (2, 0.4)}[serial]

    p = mixer(on_track)
    x = np.zeros((SR // 2, 2), np.float32)
    for track in (1, 2):
        tagged = T.tagged(x, track=track)
        p.stems.push(np.concatenate([tagged[:, :2], tagged[:, :2], tagged[:, 2:]], 1))
    loop = threading.Thread(target=p._mix_loop, daemon=True)
    loop.start()
    deadline = time.monotonic() + 10
    while p.stems.size and time.monotonic() < deadline:
        time.sleep(0.01)
    p.running = False
    loop.join(2)
    assert [t for t, _ in seen] == [1, 2] and p.semitones == 2 and p.vocal_level == 0.4
    assert seen[1][1] < SR // 2             # song 2 got its key only once song 1 had been mixed
