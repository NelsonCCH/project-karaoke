"""Open the cable input and the speaker output exactly as the app does and report what happens.

usage: python tools/check_devices.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import sounddevice as sd

from karaoke.pipeline import default_speakers, find_device

in_dev = find_device("input", "CABLE Output")
out_dev = default_speakers()
for label, dev in (("input", in_dev), ("output", out_dev)):
    d = sd.query_devices(dev)
    print(label, dev, d["name"], sd.query_hostapis(d["hostapi"])["name"], d["default_samplerate"])

peak = [0.0]


def cb(indata, frames, t, status):
    peak[0] = max(peak[0], float(np.abs(indata).max()))


for label, factory, dev in (("input", sd.InputStream, in_dev), ("output", sd.OutputStream, out_dev)):
    sr = int(sd.query_devices(dev)["default_samplerate"])
    try:
        kw = dict(device=dev, samplerate=sr, channels=2, dtype="float32")
        s = factory(callback=cb, **kw) if label == "input" else factory(**kw)
        s.start()
        time.sleep(2 if label == "input" else 0.2)
        s.stop()
        s.close()
        print(label, "OK", f"(cable peak over 2 s: {peak[0]:.3f})" if label == "input" else "")
    except Exception as e:
        print(label, "FAILED:", e)
