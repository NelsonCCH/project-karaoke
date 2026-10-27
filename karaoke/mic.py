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
  anti-feedback     always on: a narrow notch wherever the room starts to howl
  limiter          the voice never clips, however loud someone shouts
"""

import threading
from collections import deque

import numpy as np
import sounddevice as sd
from scipy import signal

from .eq import _biquad

CHUNK = 256             # samples per processing step; every reverb / echo delay is longer than this
QUEUE_MAX = 0.1         # s of mic audio kept at most if the speakers stop asking (Mic.pull keeps far less)
MIC_BLOCK = 0.002       # s per mic block in exclusive mode
MARGIN_MAX = 0.02       # s of extra queue Mic.pull may learn from gaps

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
# Anti-feedback (FeedbackSuppressor)
FB_FFT, FB_HOP = 4096, 1024     # analysis window / step at 48 kHz: ~12 Hz bins, every ~21 ms
FB_FRAMES, FB_PERSIST = 8, 6    # a peak in 6 of the last 8 analyses (~130 ms of the last 170) counts as ringing on
FB_LOW, FB_HIGH = 120, 8000     # Hz where rooms howl
FB_MIN_DB = -55                 # dBFS: quieter than this, nothing is analysed
FB_ABOVE_DB = 30                # the peak over the spectrum's median
FB_NARROW_DB = 18               # ...and over its neighbours 4-12 bins away
FB_HARMONIC_DB = 22             # ...and over every partner partial a sung note would have
FB_STEP_DB, FB_MAX_DB = 3, 24   # each detection deepens the notch by this much, down to this
FB_Q = 18                       # notch width: ~1/12 octave
FB_NOTCHES = 8
FB_HOLD, FB_RELEASE_DB = 20, 1  # s a notch stays after its howl went away, then dB/s it lets go


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


class FeedbackSuppressor:
    """Anti-feedback, as on a karaoke machine: finds a howl and cuts a narrow notch at its frequency.

    A howl is one frequency ringing on: far above the spectrum around it, lasting, and without the harmonics every
    sung note has (a voice at f always has energy at f/2, f/3, 2f/3, 3f/2 or 2f too). Each notch deepens while its
    howl persists and lets go slowly once the howl has stayed away, so a song is not left with holes in it."""

    def __init__(self, sr):
        self.sr = sr
        self.win = np.hanning(FB_FFT)
        self.buf = np.zeros(FB_FFT, np.float32)
        self.fill = 0                          # samples since the last analysis
        self.hist = deque(maxlen=FB_FRAMES)    # per analysis: the candidate peak bins
        self.notches = []                      # [freq, depth dB (negative), seconds since last heard]
        self.sos, self.zi = None, None

    def process(self, x):
        n = len(x)
        y = x
        if self.sos is not None:
            y, self.zi = signal.sosfilt(self.sos, x, zi=self.zi)
            y = y.astype(np.float32)
        # Analyse what goes to the speakers (after the notches: a notch that is deep enough stops the deepening).
        self.buf = np.concatenate([self.buf[n:], y])[-FB_FFT:]
        self.fill += n
        if self.fill >= FB_HOP:
            self._analyse(self.fill / self.sr)
            self.fill = 0
        return y

    def _analyse(self, dt):
        x = self.buf
        changed = False
        for nt in self.notches:
            nt[2] += dt
            if nt[2] > FB_HOLD and nt[1] < 0:
                nt[1] = min(0.0, nt[1] + FB_RELEASE_DB * dt)
                changed = True
        if changed:
            self.notches = [nt for nt in self.notches if nt[1] < 0]
        if np.mean(x * x) > 10 ** (FB_MIN_DB / 10):
            mag = 20 * np.log10(np.abs(np.fft.rfft(x * self.win)) + 1e-9)
            self.hist.append(self._candidates(mag))
            for b in self.hist[-1]:
                if sum(any(abs(b - c) <= 2 for c in h) for h in self.hist) >= FB_PERSIST:
                    changed |= self._cut(b, mag)
        else:
            self.hist.append([])
        if changed:
            self._design()

    def _candidates(self, mag):
        """Bins that look like a howl in this frame."""
        hz = self.sr / FB_FFT
        lo, hi = int(FB_LOW / hz), int(FB_HIGH / hz)
        floor = np.median(mag[lo:hi])
        out = []
        for b in np.argsort(mag[lo:hi])[::-1][:3] + lo:
            p = mag[b]
            if p - floor < FB_ABOVE_DB or mag[b] < mag[b - 1] or mag[b] < mag[b + 1]:
                continue
            side = np.r_[mag[max(0, b - 12) : b - 3], mag[b + 4 : b + 13]]
            if p - np.median(side) < FB_NARROW_DB:
                continue
            # A sung note's partner partials: if any is near the peak's level, this is a voice, not a howl.
            partners = [mag[max(1, int(round(b * r)) - 2) : int(round(b * r)) + 3].max()
                        for r in (1 / 3, 1 / 2, 2 / 3, 3 / 2, 2) if b * r * hz < self.sr / 2 - 3 * hz]
            if partners and p - max(partners) < FB_HARMONIC_DB:
                continue
            out.append(int(b))
        return out

    def _cut(self, b, mag):
        """Deepen the notch at bin b's frequency (or open one). True if the filter changed."""
        # Parabolic interpolation: the howl's frequency between bins.
        a, c, d = mag[b - 1], mag[b], mag[b + 1]
        f = (b + 0.5 * (a - d) / (a - 2 * c + d + 1e-12)) * self.sr / FB_FFT
        nt = next((nt for nt in self.notches if abs(np.log2(f / nt[0])) < 1 / 24), None)
        if nt is None:
            if len(self.notches) >= FB_NOTCHES:
                self.notches.remove(max(self.notches, key=lambda n: (n[1], n[2])))  # the shallowest, longest quiet
            nt = [f, 0.0, 0.0]
            self.notches.append(nt)
        nt[2] = 0.0
        if nt[1] <= -FB_MAX_DB:
            return False
        nt[1] = max(-FB_MAX_DB, nt[1] - FB_STEP_DB)
        return True

    def _design(self):
        if not self.notches:
            self.sos, self.zi = None, None
            return
        sos = np.array([_biquad("peak", f, depth, FB_Q, self.sr) for f, depth, _ in self.notches])
        if self.zi is None or len(self.zi) != len(sos):
            self.zi = np.zeros((len(sos), 2))
        self.sos = sos


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
        self.fb, self.fb_on = FeedbackSuppressor(sr), True
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
        if self.fb_on:
            y = self.fb.process(y)
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
        # The largest mic block seen, and the margin learnt from gaps: pull() keeps the queue at block + n + margin.
        self.block, self.margin, self.primed = 0, 0, False
        self.margin_max = int(MARGIN_MAX * sr)
        self.lock = threading.Lock()
        # Exclusive mode with 2 ms blocks first: it skips Windows' shared mixing. Measured on the ATR2100x: 4 ms
        # reported and callbacks never more than ~6 ms apart, against 12.7 ms and up to 21 ms apart at its default
        # 10 ms blocks (the queue below must cover the longest wait). A mic that cannot run exclusive at this rate
        # (or is in use) opens shared, converted.
        for exclusive in (True, False):
            try:
                self.stream = sd.InputStream(device=device, samplerate=sr, channels=1, dtype="float32",
                                             latency=MIC_BLOCK if exclusive else "low",
                                             blocksize=int(MIC_BLOCK * sr) if exclusive else 0, callback=self._in_cb,
                                             extra_settings=sd.WasapiSettings(exclusive=exclusive, auto_convert=not exclusive))
                self.stream.start()
                break
            except sd.PortAudioError:
                if not exclusive:
                    raise
        self.latency = float(self.stream.latency)

    def close(self):
        self.stream.stop()
        self.stream.close()

    def _in_cb(self, indata, frames, t, status):
        with self.lock:
            self.q.append(indata[:, 0].copy())
            self.size += frames
            self.block = max(self.block, frames)
            while self.size > self.max:             # the speakers stopped asking: drop the oldest
                self.size -= len(self.q.popleft())

    def _drop(self, k):
        while k > 0 and self.q:
            b = self.q[0]
            if len(b) <= k:
                self.q.popleft()
                k -= len(b)
                self.size -= len(b)
            else:
                self.q[0] = b[k:]
                self.size -= k
                k = 0

    def pull(self, n):
        x, got = np.zeros(n, np.float32), 0
        with self.lock:
            # The mic and the speakers hand over different block sizes (measured: 480 in, 240 out) on two clocks.
            # Playing whatever is queued left a speaker block short every few callbacks, each a gap: a constant
            # crackle on the voice. So the voice starts only once one mic block plus one speaker block is queued,
            # and stays that full: the oldest is dropped when the mic's clock runs ahead, and a short block
            # (the speakers' clock ran ahead) waits for the queue to fill again.
            # A mic may also hand over a period late (measured on a USB mic in exclusive mode: up to 21 ms between
            # 10 ms blocks): each gap that still happens adds one speaker block of margin, up to MARGIN_MAX.
            target = self.block + n + self.margin
            if not self.primed:
                if self.size < target:
                    return self.chain.process(x)
                self.primed = True
            self._drop(self.size - (target + self.block))
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
            if got < n:
                self.primed = False
                self.margin = min(self.margin + n, self.margin_max)
        return self.chain.process(x)
