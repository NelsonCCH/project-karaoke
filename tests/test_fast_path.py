"""Nothing competes with the singer: other players are paused, the WDM-KS open is retried, and a fallback to the
slow path is reported with what is still playing. Stand-ins only: no devices, no media sessions."""

import asyncio
import threading
from types import SimpleNamespace

import karaoke.pipeline as pipeline
import karaoke.session as session
from karaoke.pipeline import Pipeline
from karaoke.session import Session
from karaoke.transport import PLAYING, SpotifyWatcher


class FakeMediaSession:
    def __init__(self, app, playing):
        self.source_app_user_model_id, self.playing, self.paused = app, playing, False

    def get_playback_info(self):
        return SimpleNamespace(playback_status=PLAYING if self.playing else 0)

    async def try_pause_async(self):
        self.paused = True


def test_other_players_are_paused_but_not_spotify():
    sessions = [FakeMediaSession("Spotify.exe", True), FakeMediaSession("Chrome", True),
                FakeMediaSession("MSEdge", False),
                FakeMediaSession("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic", True)]
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    w = SpotifyWatcher(None, None, None)
    w._loop_obj, w._mgr = loop, SimpleNamespace(get_sessions=lambda: sessions)
    try:
        assert w.pause_others() == ["Chrome", "Microsoft.ZuneMusic_8wekyb3d8bbwe"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert [s.paused for s in sessions] == [False, True, False, True]


def test_no_media_controls_yet_pauses_nothing():
    assert SpotifyWatcher(None, None, None).pause_others() == []


class FakeStream:
    opened = []

    def __init__(self, device, **kw):
        if device in FakeStream.busy and FakeStream.busy[device] > 0:
            FakeStream.busy[device] -= 1
            raise RuntimeError("Invalid device")
        self.latency = 0.006 if device == "ks" else 0.02
        FakeStream.opened.append(device)

    def start(self):
        pass

    def close(self):
        pass


def pipe_with(monkeypatch, ks_busy_tries):
    FakeStream.opened, FakeStream.busy = [], {"ks": ks_busy_tries}
    monkeypatch.setattr(pipeline.sd, "OutputStream", FakeStream)
    monkeypatch.setattr(pipeline, "ks_twin", lambda dev: "ks")
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    p = Pipeline.__new__(Pipeline)
    p.out_dev, p.out_sr, p.low_latency = "wasapi", 48000, True
    return p


def open_with(monkeypatch, ks_busy_tries):
    p = pipe_with(monkeypatch, ks_busy_tries)
    p._open_output()
    return p


def test_the_test_open_says_when_the_speakers_are_free(monkeypatch):
    p = pipe_with(monkeypatch, ks_busy_tries=2)
    del p.out_sr                        # only open_streams sets it, and the test open comes before
    monkeypatch.setattr(pipeline.sd, "query_devices", lambda dev: {"default_samplerate": 48000.0})
    assert [p.fast_path_free() for _ in range(3)] == [False, False, True]
    monkeypatch.setattr(pipeline, "ks_twin", lambda dev: None)
    assert p.fast_path_free()           # no WDM-KS twin: nothing to wait for


def test_a_speaker_just_let_go_is_retried(monkeypatch):
    p = open_with(monkeypatch, ks_busy_tries=3)
    assert FakeStream.opened == ["ks"] and not p.ks_missed and p.out_latency == 0.006


def test_speakers_still_busy_fall_back_and_say_so(monkeypatch):
    p = open_with(monkeypatch, ks_busy_tries=99)
    assert FakeStream.opened == ["wasapi"] and p.ks_missed


def check(monkeypatch, missed, apps):
    monkeypatch.setattr(session, "playing_apps", lambda: apps)
    s = Session.__new__(Session)
    s.pipe = SimpleNamespace(ks_missed=missed)
    s._check_fast_path()
    return s.mic_warning


def test_the_warning_names_what_plays(monkeypatch):
    assert check(monkeypatch, True, ["chrome"]).startswith("chrome is playing on these speakers")
    assert check(monkeypatch, True, []).startswith("another program is playing on these speakers")
    assert check(monkeypatch, False, ["chrome"]) == ""
