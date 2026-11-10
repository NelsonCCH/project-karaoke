"""Mic presets: save the Microphone card's settings under a name, apply, update, rename, delete. No devices."""

from karaoke.session import PRESET_FIELDS, PRESET_NAME_MAX, Session

MIC = {"mic": 40, "echo": 30, "reverb_size": "Hall", "delay_level": 10, "delay_ms": 220, "delay_repeat": 35,
       "voice": "Warm", "gate": True, "comp": True}


def session():
    s = Session.__new__(Session)
    s.settings = {**MIC, "mic_device": "Microphone (USB Audio Device)", "vocal": 10, "key": 0}
    s.mic_presets, s.saves, s.applied = [], 0, []

    def save():
        s.saves += 1

    def set_(**kw):
        s.applied.append(kw)
        s.settings.update(kw)
    s.save, s.set = save, set_
    return s


def test_save_keeps_every_mic_setting_but_the_mic_itself():
    s = session()
    assert s.preset("save", name="  Ballad  ")
    assert s.mic_presets == [{"name": "Ballad", "values": MIC}] and s.saves == 1
    assert set(PRESET_FIELDS) == set(MIC)


def test_apply_sets_them_back():
    s = session()
    s.preset("save", name="Ballad")
    s.settings.update(mic=0, echo=80, voice="Bright", gate=False)
    assert s.preset("apply", 0) and s.applied == [MIC] and {k: s.settings[k] for k in MIC} == MIC


def test_update_rename_delete():
    s = session()
    s.preset("save", name="Ballad")
    s.preset("save", name="Rock")
    s.settings["echo"] = 70
    assert s.preset("update", 0) and s.mic_presets[0]["values"]["echo"] == 70
    assert s.mic_presets[1]["values"]["echo"] == 30
    assert s.preset("rename", 1, "Rock 2") and s.mic_presets[1]["name"] == "Rock 2"
    assert s.preset("delete", 0) and [p["name"] for p in s.mic_presets] == ["Rock 2"]


def test_refused_without_a_name_or_a_real_index():
    s = session()
    assert not s.preset("save", name="   ") and not s.preset("apply", 0) and not s.preset("delete", "0")
    s.preset("save", name="x" * 99)
    assert len(s.mic_presets[0]["name"]) == PRESET_NAME_MAX
    assert not s.preset("rename", 0, "") and not s.preset("delete", 5) and not s.preset("bogus", 0)
    assert s.saves == 1
