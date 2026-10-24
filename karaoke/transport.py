"""Follow Spotify's play / pause / skip through Windows' media session (the same info the volume flyout shows).

Polls ~10x a second and reports:
  on_pause()   Spotify paused or stopped
  on_play()    Spotify playing again
  on_jump()    the user skipped, went back or seeked: what is buffered no longer matches what will come
A track that simply plays to its end and rolls into the next one is NOT a jump, so its ending still plays out.
"""

import asyncio
import threading
import time
from datetime import datetime, timezone

from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
from winrt.windows.storage.streams import Buffer, InputStreamOptions

PLAYING = 4
POLL = 0.1
SEEK_TOLERANCE = 2.0     # seconds of position drift before it counts as a seek
NATURAL_END = 4.0        # a track change this close to the old track's end is the next song rolling in


class SpotifyWatcher:
    def __init__(self, on_pause, on_play, on_jump):
        self.on_pause, self.on_play, self.on_jump = on_pause, on_play, on_jump
        self.running = False
        self.has_session = False    # Spotify is open and visible to Windows' media controls
        self.now_playing = ""
        self.serial = 0             # +1 per track change; tags captured audio together with the position
        self.tracks = {}            # serial -> {"title", "artist", "album", "duration"}
        self._art = {}              # serial -> (content type, cover image bytes)
        self.shuffle = False
        self._last = (0, 0.0, time.monotonic(), False)     # (serial, position, monotonic time, playing)

    def command(self, action, value=None):
        """Press Spotify's own buttons: "next", "prev", "playpause", "pause", "shuffle" (toggle),
        or "seek" to `value` seconds into the song."""
        s, loop = getattr(self, "_session", None), getattr(self, "_loop_obj", None)
        calls = {"next": lambda: s.try_skip_next_async(), "prev": lambda: s.try_skip_previous_async(),
                 "playpause": lambda: s.try_toggle_play_pause_async(), "pause": lambda: s.try_pause_async(),
                 "shuffle": lambda: s.try_change_shuffle_active_async(not self.shuffle),
                 "seek": lambda: s.try_change_playback_position_async(int(float(value) * 10_000_000))}
        if s is None or loop is None or action not in calls:
            return False
        if action == "seek" and not isinstance(value, (int, float)):
            return False

        async def press():
            await calls[action]()

        asyncio.run_coroutine_threadsafe(press(), loop)
        return True

    def art(self, serial):
        """(content type, image bytes) of that track's cover, or None."""
        return self._art.get(serial)

    async def _fetch_art(self, serial, props):
        try:
            stream = await props.thumbnail.open_read_async()
            buf = Buffer(stream.size)
            await stream.read_async(buf, stream.size, InputStreamOptions.NONE)
            self._art[serial] = (stream.content_type or "image/png", bytes(buf))
            for old in [k for k in self._art if k < serial - 5]:     # covers are ~150 KB; keep a few
                del self._art[old]
        except Exception:
            pass                    # no cover (podcasts, local files): the page simply shows none

    def position_at(self, t):
        """(track serial, Spotify position in s) at monotonic time t, extrapolated from the latest poll."""
        serial, pos, at, playing = self._last
        return serial, pos + (t - at if playing else 0.0)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=lambda: asyncio.run(self._loop()), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    async def _loop(self):
        mgr = await Manager.request_async()
        self._loop_obj = asyncio.get_running_loop()
        playing, track, last = None, None, None     # last = (position at that moment, wall time, end)
        art_due = []                                 # monotonic times to (re)fetch the current track's cover
        while self.running:
            try:
                s = next((x for x in mgr.get_sessions() if "spotify" in x.source_app_user_model_id.lower()), None)
                self._session = s
                self.has_session = s is not None
                if s is None:
                    await asyncio.sleep(1)
                    continue
                is_playing = s.get_playback_info().playback_status == PLAYING
                props = await s.try_get_media_properties_async()
                now_track = f"{props.title} - {props.artist}"
                tl = s.get_timeline_properties()
                pos = tl.position.total_seconds()
                if is_playing:
                    # Spotify only updates the timeline on events; extrapolate from its last update.
                    pos += max(0.0, (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds())
                end = tl.end_time.total_seconds()
                now = time.monotonic()

                if track is not None and now_track != track:
                    old_pos, old_t, old_end = last
                    played_to = old_pos + (now - old_t if playing else 0)
                    if old_end - played_to > NATURAL_END:
                        self.on_jump()
                elif last is not None and is_playing:
                    # While paused the position should stand still; while playing it should advance with time.
                    expected = last[0] + (now - last[1] if playing else 0)
                    if abs(pos - expected) > SEEK_TOLERANCE:
                        self.on_jump()
                if playing is not None and is_playing != playing:
                    (self.on_play if is_playing else self.on_pause)()
                elif playing is None and not is_playing:
                    self.on_pause()

                if now_track != track:
                    self.serial += 1
                    self.tracks[self.serial] = {"title": props.title, "artist": props.artist,
                                                "album": props.album_title, "duration": end}
                    art_due = [now, now + 3]    # Spotify can update the cover a moment after the title
                if art_due and now >= art_due[0] and props.thumbnail:
                    art_due.pop(0)
                    asyncio.create_task(self._fetch_art(self.serial, props))
                info = s.get_playback_info()
                self.shuffle = bool(info.is_shuffle_active)
                playing, track, last = is_playing, now_track, (pos, now, end)
                self._last = (self.serial, pos, now, is_playing)
                self.now_playing = now_track
            except Exception:
                await asyncio.sleep(1)
            await asyncio.sleep(POLL)
