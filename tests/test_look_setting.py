"""The pages' style is kept by the PC (settings.look), so a style picked on one page shows on every page. No devices."""

from karaoke.session import Session


def session():
    s = Session.__new__(Session)
    s.settings = {"look": "", "vocal": 10, "key": 0}
    s.ins, s.outs, s.pipe, s.saves = [], [], None, 0

    def save():
        s.saves += 1
    s.save = save
    return s


def test_a_style_is_kept_and_saved():
    s = session()
    s.set(look="wkw")
    assert s.settings["look"] == "wkw" and s.saves == 1
    s.set(look="")
    assert s.settings["look"] == "" and s.saves == 2


def test_the_lyrics_offset_is_kept_as_a_number():
    s = session()
    s.settings["lyrics_offset"] = -500
    s.set(lyrics_offset="-700")
    assert s.settings["lyrics_offset"] == -700
    try:
        s.set(lyrics_offset="soon")
    except ValueError:
        pass                    # the agent's command loop logs it; nothing is stored
    assert s.settings["lyrics_offset"] == -700 and s.saves == 1


def test_anything_but_a_plain_style_id_is_refused():
    s = session()
    for bad in ("../x", "<b>", "WKW", "a" * 21, 5):
        s.set(look=bad)
    assert s.settings["look"] == "" and s.saves == 0
