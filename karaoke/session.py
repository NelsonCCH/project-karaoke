"""One karaoke session: everything between "start" and "stop", run by agent.py (the desktop window and the always-on helper).

  start   route Spotify into the cable, load the model, open the audio streams, follow Spotify's transport
  set()   vocal / volume / key / lyrics offset apply live; model / delay / speakers restart the pipeline
  stop    close the streams and put Spotify straight back on the speakers

PortAudio's WASAPI streams only open from the thread that owns the command loop, so the owner passes
`call_main(fn, *args)` and every stream open or close goes through it.
"""

import json
import os
import socket
import threading
import time

import sounddevice as sd

from . import approuting
from .lyrics import LyricsCache
from .netsink import NetSink
from .models import DEFAULT_MODEL, DEFAULT_WINDOW, MODELS, WINDOWS
from .pipeline import Pipeline, default_speakers, find_device, wasapi_devices
from .transport import SpotifyWatcher
from .winvol import default_playback_name

SETTINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
ENGINE_LOCK_PORT = 8766     # held while an engine runs, so the window and the agent never run one each
LIVE = ("vocal", "volume", "key", "lyrics_offset", "mic", "echo")     # mic / echo are applied on the Mac
RESTART = ("model", "delay", "speakers")
RESTORE_AFTER_PAUSE = 2.0   # s from asking Spotify to pause until it goes back to the speakers


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def restore_spotify():
    """Spotify back to Windows' default output, i.e. normal listening straight to the speakers."""
    try:
        approuting.route_spotify(False)
    except Exception:
        pass


class Session:
    def __init__(self, call_main, on_status=None):
        self.call_main = call_main
        self.on_status = on_status or (lambda s: None)
        self.status = ""
        saved = load_settings()
        self.settings = {
            "model": saved.get("model") if saved.get("model") in MODELS else DEFAULT_MODEL,
            "delay": saved.get("delay") if saved.get("delay") in WINDOWS else DEFAULT_WINDOW,
            "speakers": saved.get("speakers"),
            "vocal": saved.get("vocal", 0), "volume": saved.get("volume", 100),
            "key": 0, "lyrics_offset": saved.get("lyrics_offset", 0),
            "mic": 0, "echo": saved.get("echo", 30),    # mic starts muted every session: no surprise feedback
        }
        self.output = "pc"          # "pc" = the PC's speakers, "mac" = streamed to the living-room Mac
        self.sink = NetSink()
        self.pipe = None
        self.gen = 0                # bumped per restart; a model that finishes loading late is dropped
        self.active = False
        self.lock = None
        self.routing_error = ""
        self.warning = ""
        self.stopped_at = 0.0
        self.spotify_playing = True
        self.off_cable_since = None
        self.lyrics = LyricsCache()
        self.scan_devices()
        self.watcher = SpotifyWatcher(self._on_pause, self._on_play, self._on_jump)
        self.watcher.start()

    def scan_devices(self, refresh=False):
        """The cable and the speakers to choose from. refresh: ask Windows again (PortAudio otherwise keeps the
        list it read at start-up, so headphones plugged in later would never show up). Only with no stream open."""
        if refresh:
            sd._terminate()
            sd._initialize()
        self.outs = [(i, n) for i, n in wasapi_devices("output") if "CABLE" not in n]
        self.in_dev = find_device("input", "CABLE Output")
        if self.settings["speakers"] not in [n for i, n in self.outs]:
            default_out = default_speakers()
            self.settings["speakers"] = next((n for i, n in self.outs if i == default_out), None)

    # ---- lifecycle -------------------------------------------------------------------------------------------

    def start(self, output="pc"):
        if output not in ("pc", "mac"):
            return False
        if self.active:
            if output != self.output:
                self.output = output
                self.restart()
            return True
        self.output = output
        try:
            self.scan_devices(refresh=True)
        except Exception as e:
            self._status(f"Could not list the audio devices: {e}")
            return False
        if self.in_dev is None:
            self._status("VB-Audio Virtual Cable not found: install it from vb-audio.com and reboot.")
            return False
        if self.settings["speakers"] is None and output == "pc":
            self._status("No speakers or headphones found. Connect some, then press Start again.")
            return False
        try:
            self.lock = socket.socket()
            self.lock.bind(("127.0.0.1", ENGINE_LOCK_PORT))
        except OSError:
            self.lock = None
            self._status("Another Project Karaoke (window or helper) is already running the engine.")
            return False
        self.active = True
        if not approuting.pids():
            # Started from the Mac or the phone with Spotify closed on the PC: open it, so the PC shows up in
            # Spotify Connect. route_check (every 2 s) moves it onto the cable once it is running.
            try:
                os.startfile("spotify:")
            except OSError as e:
                self.routing_error = f"Could not open Spotify: {e}"
        self.route_check()
        self.restart()
        return True

    def stop(self):
        """Call on the main thread. Also pauses Spotify: stopping karaoke must not leave the song playing on, now
        unprocessed, out of the PC's speakers."""
        # Always ask: Spotify reports play / pause a second or more late, so spotify_playing may be stale.
        paused = self.active and self.watcher.command("pause")
        self.active = False
        self.stopped_at = time.monotonic()
        self.gen += 1
        self.save()
        old, self.pipe = self.pipe, None
        if old:
            old.stop()
        if paused:
            # Back to the speakers only once the pause has landed, or the song's last moment leaks out of them.
            # Until then it plays into the cable, where nobody hears it.
            threading.Timer(RESTORE_AFTER_PAUSE, lambda: self.active or restore_spotify()).start()
        else:
            restore_spotify()
        if self.lock:
            self.lock.close()
            self.lock = None
        self._status("Stopped.")

    def close(self):
        self.stop()
        self.watcher.stop()

    def restart(self):
        """Call on the main thread. Model load runs in a worker; the streams open back on the main thread."""
        self.save()
        old, self.pipe = self.pipe, None
        if old:
            old.stop()
        self.gen += 1
        gen = self.gen
        if not self.active:
            return
        s = self.settings
        out_dev = next((i for i, n in self.outs if n == s["speakers"]), None)
        pipe = Pipeline(s["model"], self.in_dev, out_dev, on_status=self._status,
                        chunk_seconds=WINDOWS[s["delay"]], clock=self.watcher.position_at,
                        sink=self.sink if self.output == "mac" else None)

        def work():
            try:
                pipe.load()
            except Exception as e:
                self._status(f"Failed to load model: {e}")
                return
            self.call_main(open_on_main)

        def open_on_main():
            if not self.active or gen != self.gen:
                pipe.sep = None     # stopped or restarted again while loading: a newer pipeline is coming
                return
            try:
                pipe.open_streams()
            except Exception as e:
                self._status(f"Failed to open audio: {e}")
                return
            self.pipe = pipe
            if not self.spotify_playing:
                pipe.pause()
            self.apply()

        threading.Thread(target=work, daemon=True).start()

    # ---- settings --------------------------------------------------------------------------------------------

    def set(self, **kw):
        """Call on the main thread (a restart may close streams)."""
        kw = {k: v for k, v in kw.items() if k in self.settings}
        if "key" in kw:
            kw["key"] = max(-12, min(12, int(kw["key"])))
        for k in ("vocal", "volume", "mic", "echo"):
            if k in kw:
                kw[k] = max(0, min(100, float(kw[k])))
        if kw.get("model") not in (None, *MODELS) or kw.get("delay") not in (None, *WINDOWS):
            return
        changed = {k for k, v in kw.items() if self.settings[k] != v}
        self.settings.update(kw)
        if changed & set(RESTART):
            self.restart()
        else:
            self.apply()
            self.save()

    def apply(self):
        p, s = self.pipe, self.settings
        if p:
            p.vocal_level = s["vocal"] / 100
            p.volume = s["volume"] / 100
            p.semitones = s["key"]

    def save(self):
        s = self.settings
        save_settings({k: (round(s[k]) if k in ("vocal", "volume", "echo") else s[k]) for k in s
                       if k not in ("key", "mic")})

    # ---- Spotify ---------------------------------------------------------------------------------------------

    # Watcher callbacks run on the watcher's thread; they only flip flags on the pipeline.
    def _on_pause(self):
        self.spotify_playing = False
        if self.pipe:
            self.pipe.pause()

    def _on_play(self):
        self.spotify_playing = True
        if self.pipe:
            self.pipe.resume()

    def _on_jump(self):
        if self.pipe:
            self.pipe.jump()

    def route_check(self):
        """While a session runs, Spotify plays into the cable; call every couple of seconds in case Spotify was
        started (or restarted) after the session. Returns a warning to show, or ""."""
        if not self.active:
            return ""
        try:
            # Not only False: a Spotify that was just opened reports None until it has played, and must still go
            # to the cable before its first song, or that song comes out of the PC's speakers.
            on = approuting.spotify_on_cable()
            if on is not True and approuting.pids():
                approuting.route_spotify(True)
            self.routing_error = ""
            # A Spotify that has not played yet reports None; one that stays off the cable while it plays would
            # be heard unprocessed on the speakers, and the karaoke would be silence.
            if on is False and self.spotify_playing:
                self.off_cable_since = self.off_cable_since or time.monotonic()
                if time.monotonic() - self.off_cable_since > 6:
                    self.routing_error = ("Spotify won't move to the cable, so you hear the original song. "
                                          "Close Spotify completely (tray icon > Quit) and open it again.")
            else:
                self.off_cable_since = None
        except Exception as e:
            self.routing_error = f"Could not route Spotify to the cable: {e}"
        name = default_playback_name() or ""
        self.warning = self.routing_error or (
            "Windows' default output is the cable, so the volume keys do nothing. "
            "Settings > System > Sound > Output: pick your speakers." if "CABLE" in name else "")
        return self.warning

    def _status(self, s):
        self.status = s
        self.on_status(s)

    # ---- what the pages show ---------------------------------------------------------------------------------

    def heard(self):
        """(track info, position in s now coming out of the speakers incl. the lyrics offset) or (None, None)."""
        h = self.pipe.heard() if self.pipe else None
        if h is None:
            return None, None
        serial, pos = h
        info = self.watcher.tracks.get(serial)
        return ({"serial": serial, **info} if info else None), pos + self.settings["lyrics_offset"] / 1000

    def state(self):
        p, w = self.pipe, self.watcher
        track, pos = self.heard()
        playing = bool(p and p.sep and p.stats["state"] == "playing" and not p.paused)
        return {
            "active": self.active, "ready": bool(p and p.sep), "status": self.status,
            "output": self.output, "player": self.sink.connected, "spotify_playing": self.spotify_playing,
            "spotify_open": self.watcher.has_session, "warning": self.warning if self.active else "",
            # What Spotify itself is at (ahead of what is heard by the delay), for the progress bar when idle.
            "spotify": {"serial": w.serial, "position": w.position_at(time.monotonic())[1],
                        **{k: (w.tracks.get(w.serial) or {}).get(k) for k in ("title", "artist", "duration")},
                        "shuffle": w.shuffle},
            "playing": playing, "track": track, "position": pos,
            "latency": p.latency if p and p.sep else None,
            "now_playing": self.watcher.now_playing, "settings": self.settings,
            "options": {"model": {k: f"{m['label']} · {m['size']}" for k, m in MODELS.items()}, "delay": list(WINDOWS),
                        "speakers": [n for i, n in self.outs]},
            "stats": dict(p.stats) if p else None,
        }

    def lyrics_for(self, serial):
        info = self.watcher.tracks.get(serial)
        if not info:
            return None
        res = self.lyrics.get(info)
        return {"serial": serial, "loading": res is None, **(res or {})}
