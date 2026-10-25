"""Synced lyrics from LRCLIB (lrclib.net, free, no key).

Spotify reports the artist as it is listed there ("Eason Chan") while LRCLIB entries are often in Chinese
("陳奕迅"), so after an exact lookup fails the match falls back to a search: first by title and artist, then by
title alone. A candidate must have the same title (ignoring case, spaces and punctuation, and a "(Live)" or
"- Remastered" tail) and a duration within a couple of seconds; synced lyrics are preferred, then the same
artist. LRCLIB answers 503 when busy, so every request is retried a few times, and a lookup it never answered
is tried again later instead of being remembered as "no lyrics".
"""

import re
import threading
import time

import requests

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "ProjectKaraoke/0.1 (personal home karaoke)"}
DURATION_SLACK = 2.5        # seconds between Spotify's and LRCLIB's duration still counted as the same recording
RETRY_AFTER, RETRIES = 15.0, 3   # seconds between lookups LRCLIB did not answer, and how many
_TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text):
    """LRC text -> sorted [(seconds, line)]. Handles several timestamps on one line and an [offset:ms] tag."""
    offset = 0.0
    m = re.search(r"\[offset:\s*([+-]?\d+)\]", text or "")
    if m:
        offset = int(m.group(1)) / 1000     # positive offset = lyrics come earlier
    out = []
    for raw in (text or "").splitlines():
        stamps = _TIME.findall(raw)
        line = _TIME.sub("", raw).strip()
        for mm, ss in stamps:
            out.append((max(0.0, int(mm) * 60 + float(ss) - offset), line))
    return sorted(out, key=lambda x: x[0])


def _get(path, params):
    for wait in (0, 1, 2, 4):
        time.sleep(wait)
        try:
            r = requests.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=8)
        except requests.RequestException:
            continue
        if r.status_code == 404:
            return None
        if r.ok:
            return r.json()
    raise LookupError(f"LRCLIB did not answer /{path}")


def _plain_title(title):
    """'K歌之王 (Live)' / 'Song - Remastered 2011' -> the bare title, for a second search."""
    return re.split(r"\s+[-–(（\[]", title, maxsplit=1)[0].strip()


def _same_title(a, b):
    def norm(s):
        return re.sub(r"[\W_]+", "", (s or "").lower())
    return bool(norm(a)) and (norm(a) == norm(b) or norm(_plain_title(a)) == norm(_plain_title(b)))


def find(title, artist, album, duration):
    """-> {"synced": [(t, line)] or [], "plain": str, "source": str} or None when LRCLIB has nothing.
    Raises LookupError when LRCLIB did not answer and nothing synced was found, so the caller can try again."""
    failed = False
    try:
        hit = _get("get", {"track_name": title, "artist_name": artist, "album_name": album, "duration": round(duration)})
    except LookupError:
        hit, failed = None, True
    if not (hit and hit.get("syncedLyrics")):
        best = None
        queries = [{"track_name": q, "artist_name": artist} for q in dict.fromkeys([title, _plain_title(title)])]
        queries += [{"track_name": q} for q in dict.fromkeys([title, _plain_title(title)])]
        for q in queries:
            try:
                found = _get("search", q) or []
            except LookupError:
                found, failed = [], True
            for c in found:
                if (abs((c.get("duration") or 0) - duration) > DURATION_SLACK or c.get("instrumental")
                        or not _same_title(c.get("trackName"), title)):
                    continue
                score = (bool(c.get("syncedLyrics")), (c.get("artistName") or "").lower() == artist.lower(),
                         -abs(c["duration"] - duration))
                if best is None or score > best[0]:
                    best = (score, c)
            if best and best[0][0]:
                break
        hit = best[1] if best else hit
    if failed and not (hit and hit.get("syncedLyrics")):
        raise LookupError("LRCLIB did not answer")
    if not hit:
        return None
    return {"synced": parse_lrc(hit.get("syncedLyrics")), "plain": hit.get("plainLyrics") or "",
            "source": f"{hit.get('trackName')} - {hit.get('artistName')}"}


class LyricsCache:
    """Fetches in the background; get() returns None while a lookup is still running, or waiting to be retried
    because LRCLIB did not answer."""

    def __init__(self):
        self.done, self.pending = {}, set()
        self.failed = {}            # key -> (attempts, time of the last one)
        self.lock = threading.Lock()

    def get(self, track):
        key = (track["title"], track["artist"], round(track["duration"]))
        with self.lock:
            if key in self.done:
                return self.done[key]
            if key in self.pending:
                return None
            n, last = self.failed.get(key, (0, 0.0))
            if n and time.monotonic() - last < RETRY_AFTER:
                return None
            self.pending.add(key)

        def work():
            try:
                res = find(track["title"], track["artist"], track.get("album") or "", track["duration"])
            except LookupError:
                with self.lock:
                    n = self.failed.get(key, (0, 0.0))[0] + 1
                    self.failed[key] = (n, time.monotonic())
                    self.pending.discard(key)
                    if n < RETRIES:
                        return
                res = None
            except Exception:
                res = None
            with self.lock:
                self.done[key] = res or {"synced": [], "plain": "", "source": ""}
                self.pending.discard(key)

        threading.Thread(target=work, daemon=True).start()
        return None
