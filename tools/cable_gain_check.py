"""Measure how much Windows attenuates audio going into CABLE Input, and compare with the endpoint's dB reading.

Plays a 997 Hz tone into CABLE Input (never to the speakers) while recording CABLE Output.
usage: python tools/cable_gain_check.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import sounddevice as sd

from karaoke.pipeline import find_device
from karaoke.winvol import EndpointReader

SR, F, AMP = 48000, 997.0, 0.5
out_dev, in_dev = find_device("output", "CABLE Input"), find_device("input", "CABLE Output")
t = np.arange(SR * 3) / SR
tone = (AMP * np.sin(2 * np.pi * F * t)).astype(np.float32)
rec = sd.playrec(np.stack([tone, tone], 1), samplerate=SR, channels=2, dtype="float32",
                 device=(in_dev, out_dev), blocking=True)
x = rec[SR : 2 * SR, 0]
# Amplitude at the tone frequency only, so Spotify playing into the cable at the same time does not matter.
amp = 2 * np.abs(np.sum(x * np.exp(-2j * np.pi * F * np.arange(len(x)) / SR))) / len(x)
measured = 20 * np.log10(max(amp, 1e-9) / AMP)
print(f"measured attenuation {measured:6.2f} dB, endpoint reports {EndpointReader('CABLE Input').gain_db():6.2f} dB")
