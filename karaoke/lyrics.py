"""Synced lyrics from LRCLIB (lrclib.net, free, no key).

Spotify reports the artist as it is listed there ("Eason Chan") while LRCLIB entries are often in Chinese
("陳奕迅"), so after an exact lookup fails the match falls back to: same title, duration within a couple of
seconds, synced lyrics preferred, artist match only as a tie-breaker. LRCLIB answers 503 when busy, so every
request is retried a few times.
"""

import re
import threading
import time

import requests

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "ProjectKaraoke/0.1 (personal home karaoke)"}
DURATION_SLACK = 2.5        # seconds between Spotify's and LRCLIB's duration still counted as the same recording
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
    return None


def _plain_title(title):
    """'K歌之王 (Live)' / 'Song - Remastered 2011' -> the bare title, for a second search."""
    return re.split(r"\s+[-–(（\[]", title, maxsplit=1)[0].strip()


def find(title, artist, album, duration):
    """-> {"synced": [(t, line)] or [], "plain": str, "source": str} or None when LRCLIB has nothing."""
    hit = _get("get", {"track_name": title, "artist_name": artist, "album_name": album, "duration": round(duration)})
    if not (hit and hit.get("syncedLyrics")):
        best = None
        for q in dict.fromkeys([title, _plain_title(title)]):
            for c in _get("search", {"track_name": q}) or []:
                if abs((c.get("duration") or 0) - duration) > DURATION_SLACK or c.get("instrumental"):
                    continue
                score = (bool(c.get("syncedLyrics")), (c.get("artistName") or "").lower() == artist.lower(),
                         -abs(c["duration"] - duration))
                if best is None or score > best[0]:
                    best = (score, c)
            if best and best[0][0]:
                break
        hit = best[1] if best else hit
    if not hit:
        return None
    return {"synced": parse_lrc(hit.get("syncedLyrics")), "plain": hit.get("plainLyrics") or "",
            "source": f"{hit.get('trackName')} - {hit.get('artistName')}"}


class LyricsCache:
    """Fetches in the background; get() returns None while a lookup is still running."""

    def __init__(self):
        self.done, self.pending = {}, set()
        self.lock = threading.Lock()

    def get(self, track):
        key = (track["title"], track["artist"], round(track["duration"]))
        with self.lock:
            if key in self.done:
                return self.done[key]
            if key in self.pending:
                return None
            self.pending.add(key)

        def work():
            try:
                res = find(track["title"], track["artist"], track.get("album") or "", track["duration"])
            except Exception:
                res = None
            with self.lock:
                self.done[key] = res or {"synced": [], "plain": "", "source": ""}
                self.pending.discard(key)

        threading.Thread(target=work, daemon=True).start()
        return None
