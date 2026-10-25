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
from .eq import DEFAULT_EQ, PRESETS
from .lyrics import LyricsCache
from .mic import DEFAULT_SIZE, DEFAULT_VOICE, SIZES, VOICES, Mic, level_gain
from .netsink import NetSink
from .models import DEFAULT_MODEL, DEFAULT_WINDOW, MODELS, WINDOWS
from .pipeline import Pipeline, default_speakers, find_device, wasapi_devices
from .transport import SpotifyWatcher
from .winvol import default_playback_name

SETTINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
ENGINE_LOCK_PORT = 8766     # held while an engine runs, so the window and the agent never run one each
DEFAULT_VOCAL = 10          # % of the original singer kept; songs never changed play at this
LIVE = ("vocal", "volume", "key", "lyrics_offset", "eq", "mic", "echo", "mic_device", "voice", "reverb_size",
        "delay_level", "delay_ms", "delay_repeat", "gate", "comp")     # mic / echo also reach the Mac player
RESTART = ("model", "delay", "speakers")
LYRICS_OFFSET = -500        # ms; lines light up this much before they are sung, as karaoke screens do
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
            "vocal": DEFAULT_VOCAL, "volume": saved.get("volume", 100),
            "key": 0, "lyrics_offset": saved.get("lyrics_offset", LYRICS_OFFSET),
            "eq": saved.get("eq") if saved.get("eq") in PRESETS else DEFAULT_EQ,
            "mic": 0, "echo": saved.get("echo", 30),    # mic starts muted every session: no surprise feedback
            # The PC's own mic (the Mac player has its own). "echo" is the reverb amount, as on the Mac.
            "mic_device": saved.get("mic_device") or "",
            "voice": saved.get("voice") if saved.get("voice") in VOICES else DEFAULT_VOICE,
            "reverb_size": saved.get("reverb_size") if saved.get("reverb_size") in SIZES else DEFAULT_SIZE,
            "delay_level": saved.get("delay_level", 0), "delay_ms": saved.get("delay_ms", 220),
            "delay_repeat": saved.get("delay_repeat", 35),
            "gate": saved.get("gate", True), "comp": saved.get("comp", True),
        }
        self.mic_error = ""
        # song_id -> semitones / vocal %, for every song whose key or vocal was changed: it comes back with the
        # song; others play at key 0 and DEFAULT_VOCAL
        self.song_keys = saved.get("song_keys") if isinstance(saved.get("song_keys"), dict) else {}
        self.song_vocals = saved.get("song_vocals") if isinstance(saved.get("song_vocals"), dict) else {}
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
        self.ins = [(i, n) for i, n in wasapi_devices("input") if "CABLE" not in n]     # microphones
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
            self._close_mic(old)
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
            self._close_mic(old)
            old.stop()
        self.gen += 1
        gen = self.gen
        if not self.active:
            return
        s = self.settings
        out_dev = next((i for i, n in self.outs if n == s["speakers"]), None)
        pipe = Pipeline(s["model"], self.in_dev, out_dev, on_status=self._status,
                        chunk_seconds=WINDOWS[s["delay"]], clock=self.watcher.position_at,
                        sink=self.sink if self.output == "mac" else None, on_track=self._on_track)

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
                pipe.low_latency = bool(self.settings["mic_device"]) and self.output == "pc"
                pipe.open_streams()
            except Exception as e:
                self._status(f"Failed to open audio: {e}")
                return
            self.pipe = pipe
            self._open_mic()
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
        for k in ("vocal", "volume", "mic", "echo", "delay_level", "delay_repeat"):
            if k in kw:
                kw[k] = max(0, min(100, float(kw[k])))
        if "delay_ms" in kw:
            kw["delay_ms"] = max(60, min(500, int(kw["delay_ms"])))
        for k in ("gate", "comp"):
            if k in kw:
                kw[k] = bool(kw[k])
        if (kw.get("model") not in (None, *MODELS) or kw.get("delay") not in (None, *WINDOWS)
                or kw.get("eq") not in (None, *PRESETS) or kw.get("voice") not in (None, *VOICES)
                or kw.get("reverb_size") not in (None, *SIZES)
                or kw.get("mic_device") not in (None, "", *[n for i, n in self.ins])):
            return
        changed = {k for k, v in kw.items() if self.settings[k] != v}
        self.settings.update(kw)
        for k in ("key", "vocal"):
            if k in kw:
                self._remember(k, kw[k])
        if changed & set(RESTART):
            self.restart()
        else:
            if "mic_device" in changed:
                self._open_mic()
            self.apply()
            self.save()

    def apply(self):
        p, s = self.pipe, self.settings
        if p:
            p.vocal_level = s["vocal"] / 100
            p.volume = s["volume"] / 100
            p.semitones = s["key"]
            p.eq_preset = s["eq"]
            if p.mic:
                c = p.mic.chain
                c.level = level_gain(s["mic"])
                c.reverb_mix = s["echo"] / 100 * 0.6
                c.reverb.size(s["reverb_size"])
                c.echo_mix = s["delay_level"] / 100 * 0.7
                c.echo.ms, c.echo.repeat = s["delay_ms"], s["delay_repeat"] / 100 * 0.8
                c.gate_on, c.comp_on = s["gate"], s["comp"]
                c.set_voice(s["voice"])

    # ---- the PC's microphone ---------------------------------------------------------------------------------

    def _close_mic(self, p):
        m, p.mic = p.mic, None
        if m:
            m.close()

    def _open_mic(self):
        """Main thread. (Re)open the chosen microphone on the running pipeline, or none."""
        p = self.pipe
        if not p:
            return
        self._close_mic(p)
        self.mic_error = ""
        name = self.settings["mic_device"]
        if not name or self.output != "pc":
            p.set_low_latency(False)
            return
        dev = next((i for i, n in self.ins if n == name), None)
        if dev is None:
            self.mic_error = f"{name} is not connected. Plug it in, then press Stop karaoke and Start karaoke."
            return
        try:
            p.set_low_latency(True)
            p.mic = Mic(dev, p.out_sr)
        except Exception as e:
            self.mic_error = f"Could not open the microphone: {e}"
            return
        self.apply()

    def mic_state(self):
        p = self.pipe
        m = p.mic if p else None
        if not m:
            return {"on": False, "error": self.mic_error}
        peak, m.chain.peak_in = m.chain.peak_in, m.chain.peak_in * 0.3      # falls back between polls
        return {"on": True, "error": "", "peak": round(peak, 4),
                "latency_ms": round((m.latency + p.out_latency) * 1000 + 6)}

    def save(self):
        s = self.settings
        save_settings({**{k: (round(s[k]) if k in ("vocal", "volume", "echo") else s[k]) for k in s
                          if k not in ("key", "vocal", "mic")}, "song_keys": self.song_keys, "song_vocals": self.song_vocals})

    # ---- key and vocal per song ----------------------------------------------------------------------------------------

    @staticmethod
    def song_id(info):
        """Same artist and title = same song, whichever album or playlist it is played from."""
        if not info or not info.get("title"):
            return None
        return f"{(info.get('artist') or '').strip().lower()} - {info['title'].strip().lower()}"

    def _key_song(self):
        """The song a key or vocal change is meant for: the one being heard, or, while nothing is heard yet (the wait
        after a skip), the one Spotify is playing, which is the next one heard."""
        h = self.pipe.heard() if self.pipe else None
        serial = h[0] if h else self.watcher.serial
        return self.song_id(self.watcher.tracks.get(serial))

    def _remember(self, field, value):
        sid = self._key_song()
        if sid is None:
            return
        store, default = (self.song_keys, 0) if field == "key" else (self.song_vocals, DEFAULT_VOCAL)
        if round(value) != default:
            store[sid] = round(value)
        else:
            store.pop(sid, None)

    def _on_track(self, serial):
        """Mixer thread, first sample of a new track: its saved key and vocal (0 and DEFAULT_VOCAL for a song never
        changed), as (semitones, vocal level 0-1)."""
        sid = self.song_id(self.watcher.tracks.get(serial))
        key, vocal = self.song_keys.get(sid, 0), self.song_vocals.get(sid, DEFAULT_VOCAL)
        self.settings["key"], self.settings["vocal"] = key, vocal
        return key, vocal / 100

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
        # A negative offset shows each line that much early, so the singer sees it coming.
        return ({"serial": serial, **info} if info else None), pos - self.settings["lyrics_offset"] / 1000

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
            "now_playing": self.watcher.now_playing, "settings": self.settings, "mic": self.mic_state(),
            "options": {"model": {k: f"{m['label']} · {m['size']}" for k, m in MODELS.items()}, "delay": list(WINDOWS),
                        "eq": list(PRESETS), "speakers": [n for i, n in self.outs],
                        "mic_devices": [n for i, n in self.ins], "voice": list(VOICES), "reverb_size": list(SIZES)},
            "stats": dict(p.stats) if p else None,
        }

    def lyrics_for(self, serial):
        info = self.watcher.tracks.get(serial)
        if not info:
            return None
        res = self.lyrics.get(info)
        return {"serial": serial, "loading": res is None, **(res or {})}
