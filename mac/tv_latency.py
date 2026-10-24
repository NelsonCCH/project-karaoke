# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["sounddevice", "numpy"]
# ///
"""How late does sound come out of whatever this Mac is playing to? (Mac speakers, or HDMI -> TV -> speakers)

Plays 6 short beeps through the current output and listens with the Mac's built-in microphone, then reports
the delay from "sent" to "heard". Run it once on the Mac's own speakers (baseline), then again with the TV as
the output; the difference is what the TV path adds. Results are appended to results.txt next to this script.

The beeps are moderate (quarter volume); turn the TV / speaker volume to a normal listening level first.
usage:  uv run tv_latency.py <label>      e.g.  uv run tv_latency.py mac-speakers
"""

import os
import sys
import time

import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 48000
GAP = 1.5            # seconds between beeps; also the longest delay this can measure
N = 6


def beep():
    t = np.arange(int(0.012 * SR)) / SR
    return (0.25 * np.sin(2 * np.pi * 2000 * t) * np.hanning(t.size)).astype(np.float32)


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "unlabelled"
    b = beep()
    sig = np.zeros(int((N * GAP + GAP) * SR), np.float32)
    starts = [int((0.5 + i * GAP) * SR) for i in range(N)]
    for s in starts:
        sig[s : s + b.size] = b
    out_name = sd.query_devices(kind="output")["name"]
    in_name = sd.query_devices(kind="input")["name"]
    print(f"output: {out_name} | microphone: {in_name} | playing {N} beeps...", flush=True)
    rec = sd.playrec(np.stack([sig, sig], 1), samplerate=SR, channels=1, dtype="float32", blocking=True)[:, 0]

    delays, quality = [], []
    for s in starts:
        seg = rec[s : s + int((GAP - 0.05) * SR)]
        corr = np.abs(np.correlate(seg, b, mode="valid"))
        k = int(corr.argmax())
        delays.append(k / SR * 1000)
        quality.append(corr[k] / (np.median(corr) + 1e-9))
    ok = [d for d, q in zip(delays, quality) if q > 8]
    with open(os.path.join(HERE, "results.txt"), "a", encoding="utf-8") as f:
        line = (f"=== latency | {label} | out: {out_name} | {time.strftime('%Y-%m-%d %H:%M')} | "
                f"each beep ms: {', '.join(f'{d:.0f}' for d in delays)} | clear beeps {len(ok)}/{N}")
        if len(ok) >= 3:
            line += f" | MEDIAN {np.median(ok):.0f} ms"
        else:
            line += " | too few clear beeps: raise the volume or move the Mac closer to the speakers and retry"
        print(line)
        f.write(line + "\n")


if __name__ == "__main__":
    main()
