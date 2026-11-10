"""Chosen speakers that are briefly missing (a TV on HDMI that is off) stay chosen. No devices."""

from karaoke import session as session_mod
from karaoke.session import Session


def session(monkeypatch, outs):
    monkeypatch.setattr(session_mod, "wasapi_devices", lambda kind: outs[0] if kind == "output" else [])
    monkeypatch.setattr(session_mod, "find_device", lambda *a: None)
    monkeypatch.setattr(session_mod, "default_speakers", lambda: 1)
    s = Session.__new__(Session)
    s.settings = {"speakers": "TV (HDMI)", "vocal": 10, "key": 0}
    s.absent_speakers = None
    s.song_keys, s.song_vocals, s.mic_presets, s.pipe = {}, {}, [], None
    return s


def test_a_missing_tv_is_kept_and_taken_back(monkeypatch):
    outs = [[(1, "Speakers (Realtek)")]]                      # the TV is off: Windows does not list it
    s = session(monkeypatch, outs)
    s.scan_devices()
    assert s.settings["speakers"] == "Speakers (Realtek)"       # plays on the default meanwhile
    saved = {}
    monkeypatch.setattr(session_mod, "save_settings", saved.update)
    s.save()
    assert saved["speakers"] == "TV (HDMI)"                     # ...but the choice is what is saved
    outs[0] = [(1, "Speakers (Realtek)"), (2, "TV (HDMI)")]     # the TV is on again
    s.scan_devices()
    assert s.settings["speakers"] == "TV (HDMI)" and s.absent_speakers is None


def test_a_new_pick_replaces_the_missing_one(monkeypatch):
    outs = [[(1, "Speakers (Realtek)"), (3, "Headphones")]]
    s = session(monkeypatch, outs)
    s.ins, s.saves = [], 0
    s.scan_devices()
    s.save = lambda: None
    s.restart = lambda: None
    s.set(speakers="Headphones")
    assert s.settings["speakers"] == "Headphones" and s.absent_speakers is None
    s.set(speakers="Not a device")
    assert s.settings["speakers"] == "Headphones"
