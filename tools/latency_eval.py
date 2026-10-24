"""How much separation quality does a shorter window (= lower latency) cost, on real songs?

Captures what is playing into the cable (in memory only, never written to disk), separates it with the
model's full window as the reference, then with shorter windows / overlaps, and reports each candidate's
SDR against that reference. Higher is closer; above ~20 dB the difference is generally hard to hear,
below ~12 dB it is usually audible.

usage: python tools/latency_eval.py [seconds] [model-key,model-key,...]   (play a song with the app open)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import sounddevice as sd
import soxr
import torch

from karaoke.engine import SR, StreamingSeparator, load_model
from karaoke.models import DEFAULT_MODEL
from karaoke.pipeline import find_device

CANDIDATES = [(11, 4), (6, 4), (4, 4), (3, 4), (2, 4), (1.5, 4), (1, 4), (0.75, 4), (0.5, 4)]
from karaoke.pipeline import BANK_MARGIN, HOLD


def capture(seconds):
    dev = find_device("input", "CABLE Output")
    sr = int(sd.query_devices(dev)["default_samplerate"])
    print(f"capturing {seconds:.0f} s from the cable...", flush=True)
    x = sd.rec(int(seconds * sr), samplerate=sr, channels=2, dtype="float32", device=dev, blocking=True)
    return soxr.resample(x, sr, SR, quality="VHQ").T.copy()


def separate(sep, mix):
    """Whole-signal run of the streaming schedule (identical output, see tests/test_streaming.py)."""
    sep.reset()
    _, v1 = sep.feed(mix)
    _, v2 = sep.flush()
    return np.concatenate([v1, v2], 1)


def sdr(ref, est):
    return 10 * np.log10(np.sum(ref ** 2) / max(np.sum((ref - est) ** 2), 1e-12))


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 75
    keys = (sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL).split(",")
    mix = capture(seconds)
    peak = np.abs(mix).max()
    print(f"captured peak {peak:.3f}")
    if peak < 0.01:
        sys.exit("nothing is playing into the cable")
    for key in keys:
        evaluate(key, mix)


def evaluate(key, mix):
    model = load_model(key)
    margin = 11 * SR                      # ignore start/end, where every schedule has fewer chunks
    ref = None
    print(f"\n{key}\n{'window':>7} {'overlap':>7} {'latency':>8} {'vocal SDR':>10} {'inst SDR':>9} {'gpu':>5} {'ms/chunk':>9}")
    for chunk_s, ov in CANDIDATES:
        sep = StreamingSeparator(key, overlap=ov, chunk_seconds=chunk_s, model=model)
        torch.cuda.synchronize()
        t = time.perf_counter()
        voc = separate(sep, mix)
        torch.cuda.synchronize()
        took = time.perf_counter() - t
        gpu = took / (mix.shape[1] / SR)
        per_chunk = took / (mix.shape[1] / sep.step) * 1000
        latency = (sep.chunk + sep.step) / SR + BANK_MARGIN + HOLD
        v, m = voc[:, margin:-margin], mix[:, margin:-margin]
        if ref is None:
            ref = v
        print(f"{chunk_s:6.2f}s {ov:7d} {latency:7.1f}s {sdr(ref, v):9.1f}  {sdr(m - ref, m - v):8.1f} {gpu:5.0%} "
              f"{per_chunk:8.0f}", flush=True)


if __name__ == "__main__":
    main()
