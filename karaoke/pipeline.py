"""Live audio path: virtual cable in -> separation -> vocal mix + key shift -> speakers.

Threads:
  capture callback   CABLE Output (what Spotify plays) -> input queue
  separation worker  input queue -> resample to 44.1 kHz -> StreamingSeparator -> stem buffer (inst, vocals)
  mixer              stem buffer -> inst + vocal_level * vocals -> pitch shift -> resample -> output buffer
  output callback    output buffer -> speakers

The mixer keeps only ~0.15 s of mixed audio ahead of the speakers, so the vocal slider and key buttons are
heard almost immediately even though the separation itself runs ~10 s behind.

Every sample carries a tag (track serial, Spotify position) from the moment it is captured, so `heard()` knows
which song position is coming out of the speakers right now whatever the delay: that is what the lyrics follow.
"""

import os
import queue
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd
import soxr

from .engine import MODEL_DIR, SR, StreamingSeparator
from .eq import DEFAULT_EQ, Equalizer
from .keyshift import KeyShifter
from .models import MODELS

MIX_BLOCK = 1024          # samples at 44.1 kHz per mixer step
OUT_AHEAD = 0.15          # seconds of mixed audio kept ready for the speakers
BANK_MARGIN = 0.3         # seconds banked beyond one hop before playback starts (GPU takes ~0.05-0.15 s per chunk
                          # at the short windows, ~0.6 s at the full one)
HOLD = 0.3                # seconds of input held back before separation, to cut pauses cleanly
SILENT = 1e-5             # Spotify sends digital silence while paused
XFADE = 2048              # samples of crossfade when the key shift switches on or off
DROP_GAP = 0.5            # seconds between drift skips at most
SEEK_LOOK = 0.5           # seconds of input looked at after a skip / seek to find where the old song really ends
SEEK_TAIL = 0.3           # ...which is within this much of the moment the jump is noticed (measured 0.10-0.19 s)
QUIET_DB = -50            # 10 ms RMS below this is the gap Spotify leaves between the old audio and the new


def wasapi_devices(kind):
    """[(index, name)] of WASAPI input or output devices."""
    apis = sd.query_hostapis()
    wasapi = next(i for i, a in enumerate(apis) if "WASAPI" in a["name"])
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["hostapi"] == wasapi and d[key] > 0]


def find_device(kind, *needles):
    for i, name in wasapi_devices(kind):
        if all(n.lower() in name.lower() for n in needles):
            return i
    return None


def default_speakers():
    """The output device to play on when nothing was picked yet: Windows' default playback device (the one the
    volume keys control), never the cable; if that is the cable itself, the first real output."""
    from .winvol import default_playback_name
    want = default_playback_name() or ""
    outs = [(i, n) for i, n in wasapi_devices("output") if "CABLE" not in n]
    for i, n in outs:
        if want and n == want:
            return i
    return outs[0][0] if outs else None


class SampleBuffer:
    """Thread-safe FIFO of stereo frames, stored as (n, ch) blocks."""

    def __init__(self, channels):
        self.blocks = deque()
        self.size = 0
        self.ch = channels
        self.lock = threading.Lock()

    def push(self, x):
        if len(x):
            with self.lock:
                self.blocks.append(x)
                self.size += len(x)

    def pop(self, n):
        """Up to n frames as (m, ch); m < n only if the buffer ran short."""
        out, got = [], 0
        with self.lock:
            while got < n and self.blocks:
                b = self.blocks[0]
                take = min(n - got, len(b))
                out.append(b[:take])
                if take == len(b):
                    self.blocks.popleft()
                else:
                    self.blocks[0] = b[take:]
                got += take
            self.size -= got
        return np.concatenate(out) if out else np.zeros((0, self.ch), np.float32)

    def clear(self):
        with self.lock:
            self.blocks.clear()
            self.size = 0


class Pipeline:
    seek_look = None            # input gathered after a jump until the old song's tail can be cut off (_cut_seek_tail)

    def __init__(self, model_key, in_dev, out_dev, on_status=None, chunk_seconds=None, clock=None, sink=None,
                 on_track=None):
        self.model_key = model_key
        # on_track(serial) -> semitones, called when the first sample of a new track reaches the mixer, so a song's
        # saved key starts exactly with that song and the end of the last one keeps its own.
        self.on_track = on_track
        self.mix_track = None
        self.mic = None             # mic.Mic, added to the speakers in _out_cb; the session opens and closes it
        self.low_latency = False    # small output buffers, set while a mic is in use
        self.sink = sink            # NetSink: play on the living-room Mac instead of out_dev
        self.chunk_seconds = chunk_seconds
        self.in_dev, self.out_dev = in_dev, out_dev
        self.on_status = on_status or (lambda s: None)
        # clock(monotonic time) -> (track serial, Spotify position in s) at that moment; see SpotifyWatcher.
        self.clock = clock or (lambda t: (0, 0.0))
        self.heard_rec = None       # (track, position of the last mixed sample, monotonic time, seconds queued)
        self.vocal_level = 0.0      # 0 = karaoke, 1 = original song
        self.volume = 1.0           # trim on top of the speakers' Windows volume, which stays the main control
        self.semitones = 0
        self.eq_preset = DEFAULT_EQ     # eq.PRESETS name; the mixer fades to a new one by itself
        self.sep = None
        self.running = False
        self.stats = {"state": "stopped", "underruns": 0, "gpu_load": 0.0, "dropped": 0}

    # ---- lifecycle -------------------------------------------------------------------------------------------

    def start(self):
        self.load()
        self.open_streams()

    def load(self):
        """Slow part (model load); safe on any thread."""
        m = MODELS[self.model_key]
        if os.path.exists(os.path.join(MODEL_DIR, m["file"])):
            self.on_status("Loading the model...")
        else:
            self.on_status(f"Downloading the model ({m['size']}, first time only). This can take a few minutes...")
        self.sep = StreamingSeparator(self.model_key, chunk_seconds=self.chunk_seconds)

    def open_streams(self):
        """Must run on the main thread: PortAudio's WASAPI streams fail to start from a worker thread here
        ('Unanticipated host error ... WDM-KS -9999')."""
        self.in_sr = int(sd.query_devices(self.in_dev)["default_samplerate"])
        self.out_sr = self.sink.sr if self.sink else int(sd.query_devices(self.out_dev)["default_samplerate"])
        self.ahead = self.sink.ahead if self.sink else OUT_AHEAD
        self.in_q = queue.Queue()
        self.stems = SampleBuffer(6)            # columns: inst L, inst R, vocal L, vocal R, track, position
        self.tags = SampleBuffer(2)             # tags of the samples inside the separator, in feed order
        self.out = self.sink or SampleBuffer(2)
        self.rs_in = soxr.ResampleStream(self.in_sr, SR, 2, dtype="float32", quality="VHQ")
        self.rs_out = soxr.ResampleStream(SR, self.out_sr, 2, dtype="float32", quality="VHQ")
        self.shifter = KeyShifter(SR)
        self.dry = SampleBuffer(2)
        self.wet_gain = 0.0
        self.eq = Equalizer(SR, self.eq_preset)
        # Separation emits a whole hop at a time, so start playing once one hop plus a margin is banked.
        self.prime = self.sep.step + int(BANK_MARGIN * SR)
        self.hold = SampleBuffer(4)            # last HOLD s of tagged input, so a pause can be cut at its exact moment
        self.paused = False
        self.trim_pause = False
        self.skip_lead = False
        self.flush_req = False
        self.mix_reset = False
        self.last_drop = 0.0
        self.playing = False
        self.out_latency = 0.0                  # the device's own output latency, known once the stream starts
        self.running = True
        self.stats.update(state="buffering", underruns=0, dropped=0)
        self.worker = threading.Thread(target=self._separate_loop, daemon=True)
        self.mixer = threading.Thread(target=self._mix_loop, daemon=True)
        self.worker.start()
        self.mixer.start()
        self.in_stream = sd.InputStream(device=self.in_dev, samplerate=self.in_sr, channels=2, dtype="float32",
                                        callback=self._in_cb, blocksize=0)
        self.in_stream.start()
        if not self.sink:
            self._open_output()
        self.on_status(f"Buffering ~{self.latency:.0f} s...")

    def _open_output(self):
        # "low" keeps the singer's own voice prompt (tens of ms); the music has its own buffer either way.
        self.out_stream = sd.OutputStream(device=self.out_dev, samplerate=self.out_sr, channels=2, dtype="float32",
                                          callback=self._out_cb, blocksize=0,
                                          latency="low" if self.low_latency else "high")
        self.out_stream.start()
        self.out_latency = float(self.out_stream.latency)

    def set_low_latency(self, on):
        """Main thread. Reopens the speakers' stream with small or normal buffers (a moment of silence)."""
        if on == self.low_latency or self.sink or not hasattr(self, "out_stream"):
            self.low_latency = on
            return
        self.low_latency = on
        self.out_stream.stop()
        self.out_stream.close()
        self._open_output()

    def stop(self):
        self.running = False
        for s in ("in_stream", "out_stream"):
            if hasattr(self, s):
                getattr(self, s).stop()
                getattr(self, s).close()
        for t in ("worker", "mixer"):
            if hasattr(self, t):
                getattr(self, t).join(timeout=5)
        self.sep = None
        self.stats["state"] = "stopped"

    @property
    def latency(self):
        return (self.sep.latency_samples + self.prime) / SR + HOLD if self.sep else 0.0

    def heard(self):
        """(track serial, Spotify position now coming out of the speakers) or None before anything played.
        Between mixer updates it runs on with the clock, but never past what was actually mixed, so it stops by
        itself on pause or an underrun."""
        r = self.heard_rec
        if r is None:
            return None
        track, pos, t, queued = r
        return track, pos - queued + min(time.monotonic() - t, queued)

    # ---- transport (called from the Spotify watcher) ---------------------------------------------------------

    def pause(self):
        """Freeze where we are: keep everything banked so resuming continues instantly."""
        self.paused = True
        self.trim_pause = True

    def resume(self):
        self.paused = False
        self.skip_lead = True

    def jump(self):
        """Skip / previous / seek: the banked audio is the wrong song now, drop it all."""
        self.flush_req = True

    # ---- threads ---------------------------------------------------------------------------------------------

    def _in_cb(self, indata, frames, t, status):
        captured = time.monotonic() - max(0.0, t.currentTime - t.inputBufferAdcTime)
        self.in_q.put((indata.copy(), captured))

    def _separate_loop(self):
        try:
            self._separate_forever()
        except Exception as e:
            # Most likely the GPU ran out of memory (a game, or a small card at a long delay). Say so instead of
            # leaving "Playing" on screen over silence.
            self.stats["state"] = "failed"
            hint = " Close other programs using the GPU, or pick a shorter delay." if "memory" in str(e).lower() else ""
            self.on_status(f"Vocal removal failed: {str(e)[:200]}.{hint} Press Stop, then Start.")

    def _separate_forever(self):
        while self.running:
            try:
                x, captured = self.in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            blocks = [x]
            while not self.in_q.empty():
                blocks.append(self.in_q.get_nowait()[0])
            y = self.rs_in.resample_chunk(np.concatenate(blocks))
            track, pos = self.clock(captured)
            tags = np.stack([np.full(len(y), track, np.float32), pos + np.arange(len(y), dtype=np.float32) / SR], 1)
            self._process_input(np.concatenate([y, tags], 1))

    def _process_input(self, x):
        """One block of tagged 44.1 kHz input (n, 4: L, R, track, position): apply transport state, separate,
        bank the stems with their tags."""
        if self.flush_req:
            self.flush_req = False
            self.sep.reset()
            self.hold.clear()
            self.tags.clear()
            self.stems.clear()
            self.out.clear()
            self.heard_rec = None
            self.mix_reset = True       # the mixer also holds old audio (shifter, dry delay, resampler)
            self.playing = False
            self.stats["state"] = "buffering"
            self.seek_look = []
        if self.trim_pause:
            # Feed what was played before the pause; drop the silence Spotify sent after it.
            self.trim_pause = False
            h = self.hold.pop(self.hold.size)
            loud = np.flatnonzero(np.abs(h[:, :2]).max(1) > SILENT)
            x = h[: loud[-1] + 1] if len(loud) else h[:0]
        elif self.paused:
            return
        else:
            if self.seek_look is not None:
                self.seek_look.append(x)
                if sum(len(b) for b in self.seek_look) < SEEK_LOOK * SR:
                    return
                x = self._cut_seek_tail(np.concatenate(self.seek_look))
                self.seek_look = None
            if self.skip_lead:
                # Spotify takes a moment to restart after "play"; skip that silence so the song joins up.
                loud = np.flatnonzero(np.abs(x[:, :2]).max(1) > SILENT)
                if not len(loud):
                    return
                x = x[loud[0]:]
                self.skip_lead = False
            self.hold.push(x)
            x = self.hold.pop(max(0, self.hold.size - int(HOLD * SR)))
        if not len(x):
            return
        t = time.perf_counter()
        before = self.sep.next_start
        self.tags.push(x[:, 2:])
        inst, voc = self.sep.feed(x[:, :2].T)
        if self.sep.next_start != before:
            hops = (self.sep.next_start - before) / self.sep.step
            self.stats["gpu_load"] = (time.perf_counter() - t) / (hops * self.sep.step / SR)
        if inst.shape[1]:
            # The separator emits exactly the samples it was fed, in order, so the oldest tags are theirs.
            tags = self.tags.pop(inst.shape[1])
            self.stems.push(np.concatenate([np.concatenate([inst, voc]).T.astype(np.float32), tags], 1))

    @staticmethod
    def _cut_seek_tail(x):
        """The first SEEK_LOOK s captured after a skip / seek was noticed. Spotify reports the new position a
        moment before its audio changes (measured 0.10-0.19 s of the old song after the report, then on a restart
        ~0.2 s of near silence), so drop up to where the old audio ends: the end of a quiet stretch, or else the
        deepest dip, if either comes within SEEK_TAIL s. With neither, the audio had already changed: keep it all."""
        hop = SR // 100
        n = len(x) // hop
        if n == 0:
            return x
        frames = x[: n * hop, :2].reshape(n, hop, 2)
        db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=(1, 2))) + 1e-9)
        look = int(SEEK_TAIL * 100)
        quiet = np.flatnonzero(db[:look] < QUIET_DB)
        if len(quiet):
            end = quiet[0]
            while end < n and db[end] < QUIET_DB:
                end += 1
            return x[end * hop :]
        m = int(np.argmin(db[:look]))
        if db[m] < np.median(db) - 12:
            return x[m * hop :]
        return x

    def _reset_mixer(self):
        """Drop every bit of audio the mixer stage still holds, so nothing of the old song leaks out."""
        self.mix_reset = False
        self.shifter = KeyShifter(SR)
        self.dry.clear()
        self.wet_gain = 0.0
        self.eq.reset()
        self.rs_out = soxr.ResampleStream(SR, self.out_sr, 2, dtype="float32", quality="VHQ")
        self.out.clear()

    def _mix_loop(self):
        while self.running:
            if self.mix_reset:
                self._reset_mixer()
            if self.paused:
                time.sleep(0.01)
                continue
            if not self.playing:
                if self.stems.size >= self.prime:
                    self.playing = True
                    self.stats["state"] = "playing"
                else:
                    time.sleep(0.05)
                    continue
            ahead = self.sink.ahead if self.sink else self.ahead     # the Mac's buffer can grow mid-song
            if self.out.size > ahead * self.out_sr:
                time.sleep(0.01)
                continue
            s = self.stems.pop(MIX_BLOCK)
            if len(s) == 0:
                time.sleep(0.01)
                continue
            # Clock drift between the cable and the speakers: if the bank grows far past target, skip one block,
            # crossfaded into the next so the cut is not heard, and at most one per DROP_GAP. A backlog (the Mac
            # stalled) then drains as a slow unheard catch-up instead of a burst of cuts that sounds fast-forwarded.
            now = time.monotonic()
            if (self.stems.size > self.prime + self.sep.step + SR and now - self.last_drop > DROP_GAP
                    and len(s) == MIX_BLOCK):
                n = self.stems.pop(MIX_BLOCK)
                if len(n) == MIX_BLOCK:
                    f = np.linspace(0.0, 1.0, MIX_BLOCK, dtype=np.float32)[:, None]
                    s = np.concatenate([s[:, :4] * (1 - f) + n[:, :4] * f, n[:, 4:]], 1)
                    self.last_drop = now
                    self.stats["dropped"] += 1
            track = int(s[0, 4])
            if track != self.mix_track:
                self.mix_track = track
                if self.on_track:
                    self.semitones = self.on_track(track)
            y = s[:, :2] + self.vocal_level * s[:, 2:4]
            # Hard ceiling at full scale: separation residue or the key shift must never produce a burst.
            self.eq.set(self.eq_preset)
            y = np.clip(self.eq.process(self._shift(y)) * self.volume, -1.0, 1.0)
            if self.mix_reset:
                continue                # a jump arrived while this block was in flight: it is old audio
            if len(y):
                self.out.push(self.rs_out.resample_chunk(y))
            # Still queued after the last sample of this block: the key-shift delay (the dry line holds exactly
            # that much), the output buffer and the device itself.
            if self.sink and not self.sink.connected:
                continue                # the player just left: its queue size means nothing now
            out_latency = self.sink.latency if self.sink else self.out_latency
            queued = self.dry.size / SR + self.out.size / self.out_sr + out_latency
            self.heard_rec = (int(s[-1, 4]), float(s[-1, 5]), time.monotonic(), queued)

    def _shift(self, y):
        """Key shift. The shifter always runs (~90 ms internal latency) and the dry signal is delayed to match it,
        so key 0 is the untouched signal and changing key never makes a gap or a jump."""
        self.dry.push(y)
        if self.semitones != 0:
            self.shifter.semitones = self.semitones
        wet = self.shifter.process(y)
        dry = self.dry.pop(len(wet))
        target = 1.0 if self.semitones != 0 else 0.0
        if len(wet) == 0 or (target == self.wet_gain and target == 0.0):
            self.wet_gain = target
            return dry
        # Short linear crossfade between dry and shifted when switching to or from key 0.
        k = np.arange(1, len(wet) + 1) / XFADE
        g = np.minimum(self.wet_gain + k, target) if target > self.wet_gain else np.maximum(self.wet_gain - k, target)
        self.wet_gain = float(g[-1])
        return (dry * (1 - g)[:, None] + wet * g[:, None]).astype(np.float32)

    def _out_cb(self, outdata, frames, t, status):
        y = self.out.pop(frames)
        if len(y) < frames:
            if self.playing and not self.paused:
                self.stats["underruns"] += 1
            outdata[: len(y)] = y
            outdata[len(y) :] = 0
        else:
            outdata[:] = y
        mic = self.mic
        if mic is not None:
            # The voice joins here, at the last moment, so it is not delayed by the music's buffers. It plays
            # through pauses and skips too: the singer can talk between songs.
            np.clip(outdata + mic.pull(frames)[:, None], -1.0, 1.0, out=outdata)
