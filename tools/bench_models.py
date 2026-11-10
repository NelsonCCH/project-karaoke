"""Download every candidate model and measure chunk size, latency and GPU speed on this machine.

usage: python tools/bench_models.py [model-key ...]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch

from karaoke.engine import SR, StreamingSeparator
from karaoke.models import MODELS


def bench(key, fp16):
    sep = StreamingSeparator(key, fp16=fp16)
    part = torch.randn(2, sep.chunk, device="cuda") * 0.1
    sep._run(part)  # warm-up
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    t = time.perf_counter()
    for _ in range(3):
        sep._run(part)
    torch.cuda.synchronize()
    per_chunk = (time.perf_counter() - t) / 3
    # Each step of audio costs one chunk forward; load < 1 means faster than real time.
    load = per_chunk / (sep.step / SR)
    vram = torch.cuda.max_memory_allocated() / 2**30
    print(f"{key:18} fp16={fp16!s:5} chunk={sep.chunk / SR:5.2f}s step={sep.step / SR:4.2f}s "
          f"forward={per_chunk:6.3f}s gpu_load={load:5.1%} vram={vram:4.1f}GB stems={sep.stems}", flush=True)
    del sep
    torch.cuda.empty_cache()


if __name__ == "__main__":
    for key in sys.argv[1:] or list(MODELS):
        for fp16 in (False, True):
            bench(key, fp16)
