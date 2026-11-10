"""Which sung notes does the room hand back louder? Measures the speakers -> room -> mic loop.

Plays a quiet 5 s sine sweep on the speakers and listens on the mic at the same time (in memory only, nothing is
written to disk), and turns the two into the room's response from the speakers to the mic. Multiplied by what
the vocal chain does at the saved settings (mic level, high-pass, voice preset), that is the loop gain at each
frequency. Where it comes near 0 dB the voice comes back around the loop in phase, and that note is boosted and
rings on after it is sung (by up to 1 / (1 - loop gain)); at 0 dB it howls.

Karaoke must be stopped first (the helper holds the mic and the speakers exclusively while it runs). Put the mic
where the singer stands, pointing as they hold it.

It also works out the room correction (eq.room_cuts): cuts where the room booms, for the music and the voice. With --save
they go to room_eq.json, used from the next Start karaoke. For that, hold the mic where people listen, at the speakers.

usage: .venv\\Scripts\\python tools\\loop_check.py [sweep level dBFS, default -20] [--save]
"""

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import sounddevice as sd
from scipy import signal

from karaoke.agent import PORT
from karaoke.eq import _biquad
from karaoke.eq import room_cuts
from karaoke.mic import VOICES, level_gain
from karaoke.pipeline import find_device, ks_twin
from karaoke.session import ROOM_EQ, load_settings

SR = 48000
F0, F1, SWEEP = 60.0, 8000.0, 5.0
PATH = ""
LEAD = 2.0                   # s of silence first: the Realtek amp fades in after silence and ate the sweep's start
LO, HI = 80, 1500            # Hz reported: where a voice's fundamentals and first partials sit


def helper_state():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=2) as r:
            return json.load(r)
    except OSError:
        return None


def sweep(level_db):
    t = np.arange(int(SWEEP * SR)) / SR
    k = SWEEP / np.log(F1 / F0)
    x = np.sin(2 * np.pi * F0 * k * (np.exp(t / k) - 1))
    fade = int(0.05 * SR)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    return (10 ** (level_db / 20) * x).astype(np.float32)


def measure(x, mic_dev, spk_dev):
    """Play x on the speakers while recording the mic; returns the recording (1 s longer than x)."""
    pad = np.zeros(int(LEAD * SR), np.float32)
    play = np.concatenate([pad, x, np.zeros(SR, np.float32)])
    rec = np.zeros(len(play), np.float32)
    pos = {"o": 0, "i": 0}

    def out_cb(outdata, frames, t, status):
        o = pos["o"]
        chunk = play[o : o + frames]
        outdata[:] = 0
        outdata[: len(chunk)] = chunk[:, None]
        pos["o"] = o + frames

    def in_cb(indata, frames, t, status):
        i = pos["i"]
        k = min(frames, len(rec) - i)
        rec[i : i + k] = indata[:k, 0]
        pos["i"] = i + frames

    ws = sd.WasapiSettings(auto_convert=True)
    # The voice's own path when the mic is on: WDM-KS, past the driver's sound effects (pipeline._open_output).
    global PATH
    with sd.InputStream(device=mic_dev, samplerate=SR, channels=1, dtype="float32", callback=in_cb, extra_settings=ws):
        out = None
        for dev, kw, name in ((ks_twin(spk_dev), {}, "WDM-KS"), (spk_dev, {"extra_settings": ws}, "WASAPI shared")):
            if dev is None:
                continue
            try:
                out = sd.OutputStream(device=dev, samplerate=SR, channels=2, dtype="float32", callback=out_cb, **kw)
                out.start()
                PATH = name
                break
            except sd.PortAudioError:
                out = None
        if out is None:
            sys.exit("Could not open the speakers.")
        time.sleep(len(play) / SR + 0.3)
        out.stop()
        out.close()
    return rec


def chain_gain(f, s):
    """|vocal chain| at frequencies f for the saved settings: mic level, 90 Hz high-pass, voice preset."""
    w = 2 * np.pi * f / SR
    _, h = signal.sosfreqz(signal.butter(2, 90, "highpass", fs=SR, output="sos"), worN=w)
    g = np.abs(h) * level_gain(s["mic"])
    bands = VOICES.get(s.get("voice", "Natural"), [])
    if bands:
        _, h = signal.sosfreqz(np.array([_biquad(*b, SR) for b in bands]), worN=w)
        g = g * np.abs(h)
    return g


def main():
    st = helper_state()
    if st and st.get("mic", {}).get("on"):
        sys.exit("Karaoke is using the mic: press Stop on the page first.")
    # The live settings when the helper runs (the mic slider is not saved: it starts at 0 every session).
    s = {**load_settings(), **((st or {}).get("settings") or {})}
    if not s.get("mic"):
        s["mic"] = 50
    nums = [a for a in sys.argv[1:] if a != "--save"]
    level_db = float(nums[0]) if nums else -20.0
    mic_dev = find_device("input", s.get("mic_device") or "\0")
    spk_dev = find_device("output", s.get("speakers") or "\0")
    if mic_dev is None or spk_dev is None:
        sys.exit(f"Could not find the mic ({s.get('mic_device')}) or the speakers ({s.get('speakers')}).")
    x = sweep(level_db)
    print("quiet first (room noise, as long as the sweep)...", flush=True)
    noise = measure(np.zeros_like(x), mic_dev, spk_dev)
    print(f"sweep at {level_db:.0f} dBFS ({SWEEP:.0f} s)...", flush=True)
    rec = measure(x, mic_dev, spk_dev)

    n = 1 << int(np.ceil(np.log2(len(rec) + len(x))))
    X, Y, N = np.fft.rfft(x, n), np.fft.rfft(rec, n), np.fft.rfft(noise, n)
    f = np.fft.rfftfreq(n, 1 / SR)
    h = np.fft.irfft(Y * np.conj(X) / (np.abs(X) ** 2 + 1e-3 * np.max(np.abs(X)) ** 2), n)
    # Keep the impulse response from just before its direct sound to 1.5 s after (the room's whole ring).
    d = int(np.argmax(np.abs(h[: SR])))
    h = h[max(0, d - 200) : d + int(1.5 * SR)]
    H = np.abs(np.fft.rfft(h, n))
    snr = 20 * np.log10(np.abs(Y) / (np.abs(N) + 1e-12) + 1e-12)
    print(f"speakers -> mic delay ({PATH}): {(d / SR - LEAD) * 1000:.0f} ms; peak mic level "
          f"{20 * np.log10(np.abs(rec).max() + 1e-12):.0f} dBFS")

    mic_level = s["mic"]
    G = H * chain_gain(np.maximum(f, 1), s)
    gdb = 20 * np.log10(G + 1e-12)
    notes = 440 * 2 ** (np.arange(-48, 24) / 12)
    notes = notes[(notes >= LO) & (notes <= HI)]
    names = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]
    rows = []
    for k, nf in enumerate(notes):
        lo, hi = np.searchsorted(f, nf / 2 ** (1 / 24)), np.searchsorted(f, nf * 2 ** (1 / 24))   # the half-semitones either side
        peak = gdb[lo:hi].max()
        at = f[lo + np.argmax(gdb[lo:hi])]
        i = int(round(12 * np.log2(nf / 440))) + 48
        name = names[i % 12] + str((i + 9) // 12)
        ok = np.median(snr[lo:hi]) > 15
        rows.append((peak, name, nf, at, ok))
    med = np.median([r[0] for r in rows])
    print(f"\nloop gain at mic {mic_level:.0f}, voice {s.get('voice')}: median over notes {med:.1f} dB "
          f"(0 dB howls; the room hands a note back louder by 1/(1-g): -6 dB -> up to +6 dB, -10 -> +3, -20 -> +1)")
    print("loudest notes (the strongest point within a half-semitone of each):")
    for peak, name, nf, at, ok in sorted(rows, reverse=True)[:10]:
        boost = -20 * np.log10(1 - min(10 ** (peak / 20), 0.99))
        print(f"  {name:4s} {nf:6.1f} Hz  loop {peak:6.1f} dB at {at:6.1f} Hz  -> up to +{boost:.1f} dB"
              + ("" if ok else "  (low signal-to-noise, rough)"))

    cuts = room_cuts(f, H, SR)
    print("\nroom correction for the music and the voice (cuts where the room booms; 1/6-octave smoothed):")
    print("  " + (", ".join(f"{c[1]:.0f} Hz {c[2]:+.1f} dB" for c in cuts) or "none needed"))
    if "--save" in sys.argv:
        with open(ROOM_EQ, "w", encoding="utf-8") as fh:
            json.dump({"speakers": s["speakers"], "measured": time.strftime("%Y-%m-%d %H:%M"),
                       "cuts": cuts}, fh, indent=1)
        print(f"  saved to {os.path.basename(ROOM_EQ)}: used from the next Start karaoke (delete the file to stop)")
    else:
        print("  (run again with --save to use it)")


if __name__ == "__main__":
    main()
