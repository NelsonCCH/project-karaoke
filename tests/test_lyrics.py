"""LRC parsing (offline) and the fuzzy LRCLIB match (network, skipped when LRCLIB is unreachable)."""

import pytest

import karaoke.lyrics as L
from karaoke.lyrics import _plain_title, _same_title, find, parse_lrc


def test_parse_lrc_multi_stamp_and_offset():
    text = "[ar:x]\n[offset:+500]\n[00:01.50][00:10.00]chorus\n[00:05.25] verse \n\n[00:20.00]"
    assert parse_lrc(text) == [(1.0, "chorus"), (4.75, "verse"), (9.5, "chorus"), (19.5, "")]


def test_plain_title():
    assert _plain_title("K歌之王 (Live)") == "K歌之王"
    assert _plain_title("Ciao - Remastered") == "Ciao"
    assert _plain_title("富士山下") == "富士山下"


def test_same_title():
    assert _same_title("Tears  (Live)", "Tears") and _same_title("tears", "Tears") and _same_title("K歌之王", "K歌之王 (Live)")
    assert not _same_title("Tears, Tears, Tears", "Tears") and not _same_title("", "")


# Seen live: LRCLIB's exact lookup did not answer, and a title-only search for "Tears" (RubberBand, 296 s) offered
# another band's "Tears, Tears, Tears" of about the same length, which was shown instead.
OTHER = {"trackName": "Tears, Tears, Tears", "artistName": "Another Band", "duration": 295.1, "plainLyrics": "wrong"}
RIGHT = {"trackName": "Tears", "artistName": "RubberBand", "duration": 295.9, "syncedLyrics": "[00:10.00]right",
         "plainLyrics": "right"}


def fake_lrclib(monkeypatch, get_answers, search):
    calls = []

    def _get(path, params):
        calls.append((path, params))
        if path == "get":
            if not get_answers:
                raise LookupError("busy")
            return None
        return search(params)
    monkeypatch.setattr(L, "_get", _get)
    return calls


def test_a_song_with_another_title_is_never_taken(monkeypatch):
    fake_lrclib(monkeypatch, True, lambda q: [OTHER])
    assert find("Tears", "RubberBand", "Apollo 18", 295.9) is None


def test_the_search_asks_with_the_artist_first(monkeypatch):
    calls = fake_lrclib(monkeypatch, True, lambda q: [OTHER, RIGHT] if q.get("artist_name") else [OTHER])
    res = find("Tears", "RubberBand", "Apollo 18", 295.9)
    assert res["source"] == "Tears - RubberBand" and res["synced"] == [(10.0, "right")]
    assert calls[1] == ("search", {"track_name": "Tears", "artist_name": "RubberBand"})


def test_no_answer_from_lrclib_is_retried_not_remembered(monkeypatch):
    fake_lrclib(monkeypatch, False, lambda q: (_ for _ in ()).throw(LookupError("busy")))
    with pytest.raises(LookupError):
        find("Tears", "RubberBand", "Apollo 18", 295.9)
    cache, track = L.LyricsCache(), {"title": "Tears", "artist": "RubberBand", "duration": 295.9}
    monkeypatch.setattr(L, "RETRY_AFTER", 0.0)
    for _ in range(200):                    # every lookup fails; after RETRIES it settles on "no lyrics"
        res = cache.get(track)
        if res is not None:
            break
        L.time.sleep(0.01)
    assert res == {"synced": [], "plain": "", "source": ""} and cache.failed[("Tears", "RubberBand", 296)][0] == L.RETRIES
    fake_lrclib(monkeypatch, True, lambda q: [RIGHT])
    fresh = L.LyricsCache()
    while (res := fresh.get(track)) is None:
        L.time.sleep(0.01)
    assert res["source"] == "Tears - RubberBand"


def test_find_matches_chinese_artist_from_spotify_english_name():
    try:
        res = find("天下無雙", "Eason Chan", "我的快樂時代", 260.7)
    except LookupError:
        res = None
    if res is None:
        pytest.skip("LRCLIB unreachable or busy")
    assert len(res["synced"]) > 10
    assert "天下無雙" in res["source"]


def wait(cache, track):
    for _ in range(500):
        res = cache.get(track)
        if res is not None:
            return res
        L.time.sleep(0.01)


def test_answers_are_kept_on_disk_for_later_sessions(monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(L, "find", lambda *a: asked.append(a[0]) or {"synced": [(10.0, "right")], "plain": "right",
                                                                      "source": "Tears - RubberBand"})
    path, track = str(tmp_path / "lyrics_cache.json"), {"title": "Tears", "artist": "RubberBand", "duration": 295.9}
    assert wait(L.LyricsCache(path), track)["source"] == "Tears - RubberBand"
    again = wait(L.LyricsCache(path), track)        # a new session: straight from the file
    assert again["synced"] == [[10.0, "right"]] and asked == ["Tears"]


def test_no_lyrics_is_asked_again_after_a_week(monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(L, "find", lambda *a: asked.append(a[0]) or None)
    path, track = str(tmp_path / "lyrics_cache.json"), {"title": "Rare", "artist": "Someone", "duration": 200.0}
    wait(L.LyricsCache(path), track)
    wait(L.LyricsCache(path), track)
    assert asked == ["Rare"]
    now = L.time.time() + L.NONE_AGAIN + 1
    monkeypatch.setattr(L.time, "time", lambda: now)
    wait(L.LyricsCache(path), track)
    assert asked == ["Rare", "Rare"]


def test_lrclib_not_answering_is_not_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "RETRY_AFTER", 0.0)
    monkeypatch.setattr(L, "find", lambda *a: (_ for _ in ()).throw(LookupError("busy")))
    wait(L.LyricsCache(str(tmp_path / "lyrics_cache.json")), {"title": "Tears", "artist": "RubberBand", "duration": 295.9})
    assert not (tmp_path / "lyrics_cache.json").exists()
