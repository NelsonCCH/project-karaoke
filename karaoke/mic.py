"""The singer's microphone on the PC, mixed straight into the speakers.

The voice must reach the speakers within a few tens of milliseconds, so it never goes through the separation's
buffers: a low-latency input stream fills a small queue, and the output stream's callback pulls from it and adds
the processed voice on top of the karaoke. The music can be seconds late (the lyrics follow it); the voice cannot.

The vocal chain, in the order a mixing desk would run it, each step block-wise in numpy:

  high-pass 90 Hz   always on: rumble, handling noise and the boom of singing close to a dynamic mic
  noise gate        optional: closes between phrases, so the mic does not keep picking up the speakers
  voice tone        a preset EQ (eq.py's biquads)
  compressor        optional: evens out loud and soft passages, like a karaoke machine's "smooth" setting
  level             the mic slider
  reverb + echo     sends mixed back in: a Freeverb-style room, and a delay with repeats ("karaoke echo")
  limiter           the voice never clips, however loud someone shouts
"""

import threading
from collections import deque

import numpy as np
import sounddevice as sd
from scipy import signal

from .eq import _biquad

CHUNK = 256             # samples per processing step; every reverb / echo delay is longer than this
QUEUE_MAX = 0.02        # s of mic audio allowed to wait for the speakers; older is dropped so the voice stays live

# name -> [(kind, Hz, dB, Q)] as in eq.py
VOICES = {
    "Natural": [],
    "Warm": [("low", 220, 3, 0.7), ("high", 7000, -3, 0.7)],
    "Bright": [("peak", 3500, 3, 1.0), ("high", 9000, 3, 0.7)],
    "Clear": [("peak", 300, -3, 1.0), ("peak", 3000, 3, 1.0)],
    "Deep": [("low", 160, 4, 0.7), ("peak", 2500, -2, 1.0)],
}
DEFAULT_VOICE = "Natural"
# reverb size -> (comb feedback, damping): tail length grows with feedback
SIZES = {"Small room": (0.74, 0.35), "Room": (0.84, 0.3), "Hall": (0.9, 0.2)}
DEFAULT_SIZE = "Room"

GATE_OPEN_DB = -48      # dBFS at the mic input (before the level slider); quieter for GATE_HOLD s = closed
GATE_HOLD = 0.25
GATE_FLOOR = 0.15       # gain when closed (-16 dB): softer than a hard mute, so word endings do not chop
COMP_THRESHOLD_DB = -24
COMP_RATIO = 3.0
COMP_MAKEUP_DB = 4
LIMIT = 0.8             # the voice's own ceiling, leaving room for the music under it


def level_gain(v):
    """Mic slider 0-100 -> linear gain: 0 = off, 50 = unchanged, 100 = +18 dB (quiet USB mics need it)."""
    return 0.0 if v <= 0 else 10 ** ((v - 50) * 0.36 / 20)


class Reverb:
    """Freeverb-style room: 4 damped combs + 2 allpasses, as on the Mac player, with a size setting."""

    def __init__(self, sr):
        s = sr / 44100
        self.combs = [np.zeros(int(d * s), np.float32) for d in (1116, 1188, 1277, 1356)]
        self.lp = [np.zeros(1) for _ in self.combs]
        self.aps = [(np.zeros(int(d * s), np.float32), np.zeros(int(d * s), np.float32)) for d in (556, 441)]
        self.size(DEFAULT_SIZE)

    def size(self, name):
        self.fb, self.damp = SIZES[name]

    def process(self, x):
        n = len(x)
        out = np.zeros(n, np.float32)
        for i, buf in enumerate(self.combs):
            # The delayed output is one continuous stream across blocks, so its one-pole low-pass (Freeverb's
            # damping: the tail darkens as it decays) can run block-wise with carried state.
            fed, self.lp[i] = signal.lfilter([1 - self.damp], [1, -self.damp], buf[:n], zi=self.lp[i])
            y = (x + self.fb * fed).astype(np.float32)
            self.combs[i] = np.concatenate([buf[n:], y])
            out += y
        out *= 0.25
        for i, (xb, yb) in enumerate(self.aps):
            y = -0.5 * out + xb[:n] + 0.5 * yb[:n]
            self.aps[i] = (np.concatenate([xb[n:], out]), np.concatenate([yb[n:], y]))
            out = y
        return out


class Echo:
    """A delay line with feedback: each repeat comes back `ms` later, `repeat` times as loud."""

    def __init__(self, sr):
        self.sr = sr
        self.buf = np.zeros(int(0.6 * sr), np.float32)      # longest echo time + margin
        self.pos = 0
        self.ms, self.repeat = 220, 0.35

    def process(self, x):
        n, L = len(x), len(self.buf)
        d = max(CHUNK, int(self.ms / 1000 * self.sr))
        idx = (self.pos - d + np.arange(n)) % L
        out = self.buf[idx].copy()
        self.buf[(self.pos + np.arange(n)) % L] = x + out * self.repeat
        self.pos = (self.pos + n) % L
        return out


class VocalChain:
    """Everything between the mic's samples and the speakers. Parameters are plain attributes, set live."""

    def __init__(self, sr):
        self.sr = sr
        self.level = 0.0                # linear, from level_gain
        self.reverb_mix, self.echo_mix = 0.3, 0.0
        self.gate_on, self.comp_on = True, True
        self.voice = None
        self.hp = signal.butter(2, 90, "highpass", fs=sr, output="sos")
        self.hp_zi = np.zeros((len(self.hp), 2))
        self.tone_sos, self.tone_zi = None, None
        self.set_voice(DEFAULT_VOICE)
        self.reverb, self.echo = Reverb(sr), Echo(sr)
        self.gate_g, self.gate_quiet = 1.0, 0.0
        self.comp_g = 1.0
        self.peak_in = 0.0              # the loudest input sample since the page last asked (for the meter)

    def set_voice(self, name):
        if name == self.voice:
            return
        self.voice = name
        bands = VOICES[name]
        self.tone_sos = np.array([_biquad(*b, self.sr) for b in bands]) if bands else None
        self.tone_zi = np.zeros((len(bands), 2)) if bands else None

    def process(self, x):
        """Mono float32 mic samples -> mono float32 voice for the speakers (same length)."""
        out = np.empty_like(x)
        for i in range(0, len(x), CHUNK):
            out[i : i + CHUNK] = self._chunk(x[i : i + CHUNK])
        return out

    def _ramp(self, g0, g1, n):
        return np.linspace(g0, g1, n, endpoint=False, dtype=np.float32) if g0 != g1 else np.float32(g1)

    def _chunk(self, x):
        n = len(x)
        self.peak_in = max(self.peak_in, float(np.abs(x).max()) if n else 0.0)
        if self.level <= 0:
            # Muted: keep the effects' tails ringing out, so a word does not cut off when the slider hits 0.
            return self.reverb.process(np.zeros(n, np.float32)) * self.reverb_mix + self.echo.process(np.zeros(n, np.float32)) * self.echo_mix
        y, self.hp_zi = signal.sosfilt(self.hp, x, zi=self.hp_zi)
        rms_db = 10 * np.log10(np.mean(y * y) + 1e-12)
        if self.gate_on:
            self.gate_quiet = 0.0 if rms_db > GATE_OPEN_DB else self.gate_quiet + n / self.sr
            target = 1.0 if self.gate_quiet < GATE_HOLD else GATE_FLOOR
            # Opens within one chunk (~5 ms), closes over ~80 ms.
            g = target if target > self.gate_g else self.gate_g + (target - self.gate_g) * min(1.0, n / self.sr / 0.08)
            y = y * self._ramp(self.gate_g, g, n)
            self.gate_g = g
        if self.tone_sos is not None:
            y, self.tone_zi = signal.sosfilt(self.tone_sos, y, zi=self.tone_zi)
        if self.comp_on:
            over = max(0.0, rms_db - COMP_THRESHOLD_DB)
            target = 10 ** ((-over * (1 - 1 / COMP_RATIO) + COMP_MAKEUP_DB) / 20)
            # ~5 ms attack, ~150 ms release, in chunk steps.
            t = 0.005 if target < self.comp_g else 0.15
            g = self.comp_g + (target - self.comp_g) * min(1.0, n / self.sr / t)
            y = y * self._ramp(self.comp_g, g, n)
            self.comp_g = g
        y = (y * self.level).astype(np.float32)
        y = y + self.reverb.process(y) * self.reverb_mix + self.echo.process(y) * self.echo_mix
        peak = float(np.abs(y).max()) if n else 0.0
        if peak > LIMIT:
            y = y * (LIMIT / peak)
        return y.astype(np.float32)


class Mic:
    """The mic input stream plus its queue. The speakers' callback calls pull(n)."""

    def __init__(self, device, sr):
        self.chain = VocalChain(sr)
        self.q, self.size = deque(), 0
        self.max = max(2 * CHUNK, int(QUEUE_MAX * sr))
        self.lock = threading.Lock()
        self.stream = sd.InputStream(device=device, samplerate=sr, channels=1, dtype="float32", latency="low",
                                     callback=self._in_cb, extra_settings=sd.WasapiSettings(auto_convert=True))
        self.stream.start()
        self.latency = float(self.stream.latency)

    def close(self):
        self.stream.stop()
        self.stream.close()

    def _in_cb(self, indata, frames, t, status):
        with self.lock:
            self.q.append(indata[:, 0].copy())
            self.size += frames
            while self.size > self.max:             # the speakers fell behind the mic's clock: drop the oldest
                self.size -= len(self.q.popleft())

    def pull(self, n):
        x, got = np.zeros(n, np.float32), 0
        with self.lock:
            while got < n and self.q:
                b = self.q[0]
                k = min(n - got, len(b))
                x[got : got + k] = b[:k]
                if k == len(b):
                    self.q.popleft()
                else:
                    self.q[0] = b[k:]
                got += k
            self.size -= got
        return self.chain.process(x)
