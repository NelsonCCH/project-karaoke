"""Which model leaves the cleanest instrumental? Scores every model against songs whose true instrumental is known.

Each song is a folder with mixture.wav and either instrumental.wav or vocals.wav (instrumental = mixture - vocals,
so backing vocals count as vocals: the target is music with no voice at all). MUSDB18-HQ's test folders work as
they are. From each song the EXCERPT seconds with the most vocal energy are scored, separated with the app's own
streaming schedule at each window, with PAD seconds of context either side so every scored sample has full overlap.

Score: SDR of the estimated instrumental against the true one (dB, higher is better; +1 dB is a clear step,
+3 dB is large). The table shows the mean over songs and the worst song, per model and window.

usage: python tools/benchmark_models.py DATASET_DIR [model,model,...] [window,window,...]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import soundfile as sf
import soxr
import torch

from karaoke.engine import SR, StreamingSeparator, load_model
from karaoke.models import MODELS

EXCERPT, PAD = 60.0, 8.0


def sdr(ref, est):
    return 10 * np.log10(np.sum(ref ** 2) / max(np.sum((ref - est) ** 2), 1e-12))


def read(path):
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return soxr.resample(x, sr, SR, quality="VHQ") if sr != SR else x


def load_song(folder):
    mix = read(os.path.join(folder, "mixture.wav"))
    if os.path.exists(os.path.join(folder, "instrumental.wav")):
        inst = read(os.path.join(folder, "instrumental.wav"))
    else:
        inst = mix - read(os.path.join(folder, "vocals.wav"))
    n = min(len(mix), len(inst))
    mix, inst = mix[:n], inst[:n]
    # The excerpt with the most vocal energy: that is where removal quality shows.
    voc = ((mix - inst) ** 2).sum(1)
    win, hop = int(EXCERPT * SR), SR
    pad = int(PAD * SR)
    if n < win + 2 * pad:
        return None
    starts = range(pad, n - win - pad, hop)
    cum = np.concatenate([[0], np.cumsum(voc)])
    s = max(starts, key=lambda a: cum[a + win] - cum[a])
    return mix[s - pad : s + win + pad].T.copy(), inst[s - pad : s + win + pad].T.copy()


def separate(sep, mix):
    """Whole-signal run of the streaming schedule (identical to live, tests/test_streaming.py)."""
    sep.reset()
    i1, _ = sep.feed(mix)
    i2, _ = sep.flush()
    return np.concatenate([i1, i2], 1)


def main():
    root = sys.argv[1]
    keys = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else list(MODELS)
    windows = [float(w) for w in sys.argv[3].split(",")] if len(sys.argv) > 3 else [2.0, 6.0]
    songs = []
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        has = lambda f: os.path.isfile(os.path.join(folder, f))
        if has("mixture.wav") and (has("instrumental.wav") or has("vocals.wav")):
            s = load_song(folder)
            if s:
                songs.append((name, *s))
    print(f"{len(songs)} songs, {EXCERPT:.0f} s each, windows {windows}", flush=True)
    pad = int(PAD * SR)
    results = {}                            # (key, window) -> [sdr per song]
    for key in keys:
        model = load_model(key)
        for w in windows:
            sep = StreamingSeparator(key, chunk_seconds=w, model=model)
            scores, t = [], time.perf_counter()
            for name, mix, inst in songs:
                est = separate(sep, mix)
                scores.append(sdr(inst[:, pad:-pad], est[:, pad:-pad]))
            results[(key, w)] = scores
            print(f"{key:26s} window {w:4.1f} s   mean {np.mean(scores):6.2f} dB   worst {min(scores):6.2f} dB   "
                  f"({time.perf_counter() - t:.0f} s)", flush=True)
        del model
        torch.cuda.empty_cache()
    print("\nper song (dB):")
    cols = list(results)
    print(f"{'song':40s}" + "".join(f"{k[:10]}@{w:g}".rjust(14) for k, w in cols))
    for i, (name, _, _) in enumerate(songs):
        print(f"{name[:40]:40s}" + "".join(f"{results[c][i]:14.2f}" for c in cols))


if __name__ == "__main__":
    main()
