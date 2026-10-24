"""Run the live pipeline without the window and print its stats once a second.

usage: python tools/run_headless.py [seconds] [model-key] [--thread]
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from karaoke.models import DEFAULT_MODEL
from karaoke.pipeline import Pipeline, default_speakers, find_device

args = [a for a in sys.argv[1:] if not a.startswith("--")]
seconds = float(args[0]) if args else 30
key = args[1] if len(args) > 1 else DEFAULT_MODEL
p = Pipeline(key, find_device("input", "CABLE Output"), default_speakers(), on_status=print)
p.volume = 0.3
if "--thread" in sys.argv:
    t = threading.Thread(target=p.start)
    t.start()
    t.join()
else:
    p.start()
try:
    for _ in range(int(seconds)):
        time.sleep(1)
        st = p.stats
        print(f"{st['state']:9} gpu {st['gpu_load']:.0%} bank {p.stems.size / 44100:4.1f}s out {p.out.size} "
              f"underruns {st['underruns']} skips {st['dropped']} inq {p.in_q.qsize()}", flush=True)
finally:
    p.stop()
