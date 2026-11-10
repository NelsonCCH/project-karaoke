"""The singer's microphone on the PC, mixed straight into the speakers.

The voice must reach the speakers within a few tens of milliseconds, so it never goes through the separation's
buffers: a low-latency input stream fills a small queue, and the output stream's callback pulls from it and adds
the processed voice on top of the karaoke. The music can be seconds late (the lyrics follow it); the voice cannot.

The vocal chain, in the order a mixing desk would run it, each step block-wise in numpy:

  high-pass 90 Hz   always on: rumble, handling noise and the boom of singing close to a dynamic mic
  noise gate        optional: closes between phrases, so the mic does not keep picking up the speakers
  room correction   cuts where the room booms (eq.room_cuts; the music gets the same, in eq.Equalizer)
  voice tone        a preset EQ (eq.py's biquads)
  compressor        optional: evens out loud and soft passages, like a karaoke machine's "smooth" setting
  level             the mic slider
  reverb + echo     sends mixed back in: a Freeverb-style room, and a delay with repeats ("karaoke echo")
  anti-feedback     always on: a narrow notch wherever the room starts to howl
  limiter          the voice never clips, however loud someone shouts
"""

import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd
from scipy import signal

from .eq import _biquad

CHUNK = 256             # samples per processing step; every reverb / echo delay is longer than this
QUEUE_MAX = 0.1         # s of mic audio kept at most if the speakers stop asking (Mic.pull keeps far less)
MIC_BLOCK = 0.002       # s per mic block in exclusive mode
MARGIN_MAX = 0.02       # s of extra queue Mic.pull may learn from gaps
DRIFT_WINDOW = 0.5      # s: Mic.pull measures clock drift by the queue's lowest point over this long
DRIFT_RATE = 0.002      # the most Mic.pull speeds up or slows down the mic to follow the speakers' clock

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
FB_STABLE = 0.002               # ...and its pitch held within 0.2% (~3.5 cents) over those analyses
FB_STEP_DB, FB_MAX_DB = 3, 24   # each detection deepens the notch by this much, down to this
FB_Q = 18                       # notch width: ~1/12 octave
FB_NOTCHES = 8
FB_HOLD, FB_RELEASE_DB = 20, 1  # s a notch stays after its howl went away, then dB/s it lets go
# Voice vs music (Balance)
BAL_BLOCK = 0.1                 # s per measurement
BAL_HP = 200                    # Hz: the music is measured above this, where it competes with a voice
BAL_SINGING_DB = -45            # dBFS at the mic input: quieter is not singing
BAL_KEEP = 1800                 # s of singing kept (numbers only)


def level_gain(v):
    """Mic slider 0-100 -> linear gain: 0 = off, 50 = unchanged, 100 = +18 dB (quiet USB mics need it)."""
    return 0.0 if v <= 0 else 10 ** ((v - 50) * 0.36 / 20)


class Reverb:
    """Freeverb's room: 8 damped combs + 4 allpasses, with a size setting.

    It had 4 combs + 2 allpasses (as the Mac player still has). Three of those 4 comb delays (1214, 1293, 1390 and
    1476 samples at 48 kHz) are near-whole multiples of one period, 86.8 samples, so their resonances pile up at
    553 Hz (C#5) and its octave below (260 / 277 Hz, C4 / C#4): measured from the impulse response at Hall, those
    lines stood 15-18 dB over the median and rang ~1.7 s against 1.25 s for the rest. Sung on those notes, the voice
    came back as a ringing bowl. Now 8 combs, with delays (Freeverb's range) searched so that their summed
    response, smoothed over a held note's pitch scatter (+-0.5%), stands at most ~6 dB over its median anywhere in
    100-1200 Hz: 12 dB with the old 4, 9.6 with Freeverb's own 8 (see tests)."""

    COMBS = (1108, 1206, 1343, 1394, 1428, 1457, 1528, 1543)
    ALLPASSES = (556, 441, 341, 277)    # Freeverb's last is 225; every delay must exceed CHUNK even at 44.1 kHz

    def __init__(self, sr):
        s = sr / 44100
        self.combs = [np.zeros(int(d * s), np.float32) for d in self.COMBS]
        self.lp = [np.zeros(1) for _ in self.combs]
        self.aps = [(np.zeros(int(d * s), np.float32), np.zeros(int(d * s), np.float32)) for d in self.ALLPASSES]
        # The 4-comb version summed x + mean of the combs; with 8 the tails are averaged twice as thin (-3 dB), so
        # they are scaled back up to the old loudness, and the voice's own share stays exactly x.
        self.wet = np.sqrt(len(self.combs) / 4) / len(self.combs)
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
            out += self.fb * fed
        out = (x + self.wet * out).astype(np.float32)
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

    A howl is one frequency ringing on: far above the spectrum around it, lasting, without the harmonics every
    sung note has (a voice at f always has energy at f/2, f/3, 2f/3, 3f/2 or 2f too), and dead steady in pitch:
    the room and the delay set it, while a voice wanders by several cents even on a straight note. Live, held low
    notes (~190 Hz) passed every other test and were cut by up to 10 dB. Each notch deepens while its howl
    persists and lets go slowly once the howl has stayed away, so a song is not left with holes in it."""

    def __init__(self, sr):
        self.sr = sr
        self.win = np.hanning(FB_FFT)
        self.buf = np.zeros(FB_FFT, np.float32)
        self.fill = 0                          # samples since the last analysis
        self.hist = deque(maxlen=FB_FRAMES)    # per analysis: the candidate peaks, (bin, interpolated Hz)
        self.recent = deque(maxlen=20)         # (unix time, what): cuts and skipped steady-but-wandering peaks
        self.skipped = {}                      # Hz rounded -> when last logged, so a held note logs once
        self.notches = []                      # [freq, depth dB (negative), seconds since last heard]
        self.sos, self.zi = None, None
        self.changes = 0                       # filter redesigns (each a coefficient switch)

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
            for b, f, why in self.hist[-1]:
                seen = [next(g for c, g, _ in h if abs(b - c) <= 2) for h in self.hist
                        if any(abs(b - c) <= 2 for c, _, _ in h)]
                if len(seen) < FB_PERSIST:
                    continue
                wander = (max(seen) - min(seen)) / f
                if wander > FB_STABLE:
                    now = time.time()
                    if now - self.skipped.get(round(f / 10), 0) > 2:
                        self.skipped[round(f / 10)] = now
                        self.recent.append((round(now, 3), f"voice {f:.0f} Hz kept (pitch moved {wander:.2%})"))
                    continue
                if self._cut(b, f):
                    changed = True
                    self.recent.append((round(time.time(), 3), f"howl {f:.0f} Hz cut ({why}, pitch {wander:.2%})"))
        else:
            self.hist.append([])
        if changed:
            self._design()
            self.changes += 1

    def _candidates(self, mag):
        """Peaks that look like a howl in this frame: (bin, interpolated Hz, the margins they passed by)."""
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
            # Parabolic interpolation: the peak's frequency between bins.
            a, c, d = mag[b - 1], mag[b], mag[b + 1]
            f = (b + 0.5 * (a - d) / (a - 2 * c + d + 1e-12)) * hz
            why = f"+{p - floor:.0f} dB, narrow +{p - np.median(side):.0f}, partials -{p - max(partners or [-1e9]):.0f}"
            out.append((int(b), float(f), why))
        return out

    def _cut(self, b, f):
        """Deepen the notch at f (bin b), or open one. True if the filter changed."""
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


class Balance:
    """How loud the voice plays against the music, in numbers only: no audio is kept.

    A singer cannot judge it from inside the room (their own head is louder than the speakers), so each BAL_BLOCK
    of singing records three levels: the mic's input (how hard they sing), the voice at the speakers (after level,
    effects and limiter) and the music at the speakers. The music is measured above BAL_HP, where a voice has to
    cut through: bass under a voice does not mask it, and would make every voice read as too quiet."""

    def __init__(self, sr):
        self.sr = sr
        self.block = int(BAL_BLOCK * sr)
        self.hp = signal.butter(2, BAL_HP, "highpass", fs=sr, output="sos")
        self.hp_zi = np.zeros((len(self.hp), 2))
        self.rows = deque(maxlen=int(BAL_KEEP / BAL_BLOCK))    # (unix time, track, input dB, voice dB, music dB)
        self._reset()

    def _reset(self):
        self.n, self.e_in, self.e_voice, self.e_music = 0, 0.0, 0.0, 0.0

    def add(self, music, mic_in, voice, track):
        """One speaker callback's worth: music (n, 2) as it goes out, the mic's samples and the voice made of them."""
        m, self.hp_zi = signal.sosfilt(self.hp, music.mean(axis=1), zi=self.hp_zi)
        self.e_music += float(np.dot(m, m))
        self.e_in += float(np.dot(mic_in, mic_in))
        self.e_voice += float(np.dot(voice, voice))
        self.n += len(voice)
        if self.n < self.block:
            return
        to_db = lambda e: 10 * np.log10(e / self.n + 1e-12)
        i, v, mu = to_db(self.e_in), to_db(self.e_voice), to_db(self.e_music)
        self._reset()
        # Singing, not the mic hearing the speakers: the room hands the music back ~25 dB down (measured), so a
        # mic this far over the music is a voice.
        if i > BAL_SINGING_DB and i > mu - 15:
            self.rows.append((round(time.time(), 1), track, round(i, 1), round(v, 1), round(mu, 1)))

    def summary(self, track):
        """The levels while singing that track: {"seconds", "over" (voice minus music, dB: median, loud 10%, ...)}."""
        r = np.array([x[2:] for x in self.rows if x[1] == track]) if track is not None else np.zeros((0, 3))
        if len(r) < 10:
            return None
        over = r[:, 1] - r[:, 2]
        p = lambda a, q: round(float(np.percentile(a, q)), 1)
        return {"seconds": round(len(r) * BAL_BLOCK), "over_median": p(over, 50), "over_loud10": p(over, 90),
                "over_quiet10": p(over, 10), "input_median": p(r[:, 0], 50), "input_loud5": p(r[:, 0], 95),
                "voice_loud5": p(r[:, 1], 95), "music_median": p(r[:, 2], 50)}


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
        self.room, self.room_sos, self.room_zi = [], None, None
        self.reverb, self.echo = Reverb(sr), Echo(sr)
        self.fb, self.fb_on = FeedbackSuppressor(sr), True
        self.gate_g, self.gate_quiet = 1.0, 0.0
        self.comp_g = 1.0
        self.peak_in = 0.0              # the loudest input sample since the page last asked (for the meter)
        self.limited = 0                # chunks the limiter turned down
        self.lim_g = 1.0

    def set_voice(self, name):
        if name == self.voice:
            return
        self.voice = name
        bands = VOICES[name]
        self.tone_sos = np.array([_biquad(*b, self.sr) for b in bands]) if bands else None
        self.tone_zi = np.zeros((len(bands), 2)) if bands else None

    def set_room(self, cuts):
        """The room correction's cuts (eq.room_cuts), or [] for none."""
        cuts = [tuple(c) for c in cuts]
        if cuts == self.room:
            return
        self.room = cuts
        self.room_sos = np.array([_biquad(*c, self.sr) for c in cuts]) if cuts else None
        self.room_zi = np.zeros((len(cuts), 2)) if cuts else None

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
        if self.room_sos is not None:
            y, self.room_zi = signal.sosfilt(self.room_sos, y, zi=self.room_zi)
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
        # Limiter: turning a whole chunk down by its own peak (as this did) jumped the gain at chunk edges, a
        # click on every loud note. The gain now ramps down to the chunk's need and back up over ~100 ms; the few
        # samples the ramp's start still lets past LIMIT are clipped.
        peak = float(np.abs(y).max()) if n else 0.0
        want = min(1.0, LIMIT / peak) if peak > 0 else 1.0
        g = want if want < self.lim_g else self.lim_g + (want - self.lim_g) * min(1.0, n / self.sr / 0.1)
        if want < 1.0:
            self.limited += 1
        y = np.clip(y * self._ramp(self.lim_g, g, n), -LIMIT, LIMIT)
        self.lim_g = g
        return y.astype(np.float32)


class Mic:
    """The mic input stream plus its queue. The speakers' callback calls pull(n)."""

    def __init__(self, device, sr):
        self.chain = VocalChain(sr)
        self.balance = Balance(sr)
        self.last_x = np.zeros(0, np.float32)  # the mic samples behind the last pull, for Balance
        self.q, self.size = deque(), 0
        self.max = max(2 * CHUNK, int(QUEUE_MAX * sr))
        # The largest mic block seen, and the margin learnt from gaps: pull() keeps the queue at block + n + margin.
        self.block, self.margin, self.primed = 0, 0, False
        self.margin_max = int(MARGIN_MAX * sr)
        # The lowest the queue got in the current DRIFT_WINDOW, and how much of the window has passed.
        self.low, self.window, self.window_len = None, 0, int(DRIFT_WINDOW * sr)
        # Every event that can click on the voice, for the page's diagnostics (session.mic_state).
        self.stats = {"gaps": 0, "drops": 0, "dropped": 0, "overflow": 0, "capped": 0, "mic_late": 0, "speaker_late": 0}
        self.recent = deque(maxlen=40)          # (unix time, what): the last such events, to match a click heard
        self.last_in = self.last_out = None
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

    def _note(self, what, count=None):
        self.stats[count or what] += 1
        self.recent.append((round(time.time(), 3), what))

    def _late(self, last, now, what):
        """A callback more than 8 ms after the one before (4 of the 2 ms blocks): worth a line in recent."""
        if last is not None and now - last > 0.008:
            self._note(f"{what} {(now - last) * 1000:.0f} ms", what)

    def _in_cb(self, indata, frames, t, status):
        now = time.perf_counter()
        self._late(self.last_in, now, "mic_late")
        self.last_in = now
        if status and status.input_overflow:
            self._note("overflow")
        with self.lock:
            self.q.append(indata[:, 0].copy())
            self.size += frames
            self.block = max(self.block, frames)
            if self.size > self.max:
                self._note("capped")
            while self.size > self.max:             # the speakers stopped asking: drop the oldest
                self.size -= len(self.q.popleft())

    def _take(self, n):
        x, got = np.zeros(n, np.float32), 0
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
        return x, got

    def pull(self, n):
        now = time.perf_counter()
        self._late(self.last_out, now, "speaker_late")
        self.last_out = now
        with self.lock:
            # The mic and the speakers hand over different block sizes (measured: 480 in, 240 out) on two clocks.
            # Playing whatever is queued left a speaker block short every few callbacks, each a gap: a constant
            # crackle on the voice. So the voice starts only once one mic block plus one speaker block is queued,
            # and stays that full: the oldest is dropped when the mic's clock runs ahead, and a short block
            # (the speakers' clock ran ahead) waits for the queue to fill again.
            # A mic may also hand over a period late (measured on a USB mic in exclusive mode: up to 21 ms between
            # 10 ms blocks): each gap that still happens adds one speaker block of margin, up to MARGIN_MAX.
            # The two clocks also drift apart for good (measured live: the ATR2100x ~130 ppm fast against the
            # Realtek). Cutting the excess out, even crossfaded, was heard as a click on a sung note every 10-20 s.
            # Instead the lowest the queue got in each DRIFT_WINDOW is held at target - one speaker block, its value
            # when both clocks agree, by playing the mic a hair fast or slow: now and then one speaker block is
            # read from one sample more (or less) and resampled. At most DRIFT_RATE of the time, so the voice's
            # pitch moves by under 4 cents and its waveform never jumps.
            target = self.block + n + self.margin
            if not self.primed:
                if self.size < target:
                    self.last_x = np.zeros(n, np.float32)
                    return self.chain.process(self.last_x)
                self.primed, self.low, self.window, self.drift, self.since = True, None, 0, 0, 0
            self.low = self.size if self.low is None else min(self.low, self.size)
            self.window += n
            if self.window >= self.window_len:
                dev = self.low - (target - n)
                self.drift = dev if abs(dev) > self.block // 2 else 0
                if self.drift:
                    self._note(f"drift {dev:+d}", "drops")
                self.low, self.window = None, 0
            self.since += 1
            step = 0
            if self.drift and self.since * n * DRIFT_RATE >= 1 and (self.drift < 0 or self.size > n + 1):
                step = 1 if self.drift > 0 else -1
                self.drift -= step
                self.since = 0
                self.stats["dropped"] += step
            if step:
                a, got = self._take(n + step)
                if got == n + step:
                    x = np.interp(np.arange(n) * (n + step) / n, np.arange(n + step), a).astype(np.float32)
                    got = n
                else:
                    x = a[:n] if step > 0 else np.concatenate([a, np.zeros(1, np.float32)])
            else:
                x, got = self._take(n)
            if got < n:
                self.primed = False
                self.margin = min(self.margin + n, self.margin_max)
                self._note(f"gap {n - got}", "gaps")
        self.last_x = x
        return self.chain.process(x)
