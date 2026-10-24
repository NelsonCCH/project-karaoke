"""LRC parsing (offline) and the fuzzy LRCLIB match (network, skipped when LRCLIB is unreachable)."""

import pytest

from karaoke.lyrics import _plain_title, find, parse_lrc


def test_parse_lrc_multi_stamp_and_offset():
    text = "[ar:x]\n[offset:+500]\n[00:01.50][00:10.00]chorus\n[00:05.25] verse \n\n[00:20.00]"
    assert parse_lrc(text) == [(1.0, "chorus"), (4.75, "verse"), (9.5, "chorus"), (19.5, "")]


def test_plain_title():
    assert _plain_title("K歌之王 (Live)") == "K歌之王"
    assert _plain_title("Ciao - Remastered") == "Ciao"
    assert _plain_title("富士山下") == "富士山下"


def test_find_matches_chinese_artist_from_spotify_english_name():
    res = find("天下無雙", "Eason Chan", "我的快樂時代", 260.7)
    if res is None:
        pytest.skip("LRCLIB unreachable or busy")
    assert len(res["synced"]) > 10
    assert "天下無雙" in res["source"]
