# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["torch", "audio-separator", "librosa==0.11.0", "audioread", "numpy", "onnxruntime"]
# ///
"""Can this Mac run Project Karaoke's vocal-removal models live?

Times one forward pass of each model on the Apple GPU (MPS) at several window lengths, and prints the share of
real time each live setting would need (must stay well under 100%). Results are appended to results.txt next
to this script.

usage (Terminal, in this folder):  uv run mac_bench.py
"""

import logging
import os
import platform
import socket
import subprocess
import sys
import time

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.expanduser("~/karaoke-bench/models")   # ~1 GB per model, kept out of the repository folder
SR = 44100
# librosa 1.0 (uv's pick on macOS) dropped audioread, which audio-separator still imports, hence the pins above.
MODELS = {
    "becruily-karaoke": "mel_band_roformer_karaoke_becruily.ckpt",   # what the PC runs by default
    "kim-vocals": "vocals_mel_band_roformer.ckpt",
}
WINDOWS = [None, 4, 2, 1]       # None = model's own window (~11 s); 1 s is the PC's default (~2 s delay)
OVERLAPS = [2, 4]               # the PC uses 4


def log(line, out):
    print(line, flush=True)
    out.write(line + "\n")
    out.flush()


def chip():
    try:
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    except Exception:
        return platform.processor()


def main():
    from audio_separator.separator import Separator

    # Separator() insists on ffmpeg being on PATH, but only for writing audio files, which this bench never does.
    Separator.check_ffmpeg_installed = lambda self: None
    if not torch.backends.mps.is_available():
        sys.exit("MPS (Apple GPU) is not available in this PyTorch build")
    dev = torch.device("mps")
    with open(os.path.join(HERE, "results.txt"), "a", encoding="utf-8") as out:
        mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        log(f"\n=== model bench | {socket.gethostname()} | {chip()} | {mem:.0f} GB | macOS {platform.mac_ver()[0]} "
            f"| torch {torch.__version__} | {time.strftime('%Y-%m-%d %H:%M')}", out)
        for key, fname in MODELS.items():
            sep = Separator(model_file_dir=MODEL_DIR, output_dir=MODEL_DIR, log_level=logging.WARNING)
            sep.load_model(model_filename=fname)
            inst = sep.model_instance
            model = inst.model_run.to(dev).eval()
            cfg = inst.model_data_cfgdict
            hop = int(getattr(cfg.model, "stft_hop_length", None) or cfg.audio.hop_length)
            native = hop * (int(cfg.inference.dim_t) - 1)
            for w in WINDOWS:
                chunk = native if w is None else hop * round(w * SR / hop)
                for half in (False, True):
                    try:
                        x = (torch.randn(1, 2, chunk) * 0.1).to(dev)
                        with torch.no_grad(), torch.autocast("mps", dtype=torch.float16, enabled=half):
                            model(x)                       # warm-up
                            torch.mps.synchronize()
                            t = time.perf_counter()
                            for _ in range(2):
                                model(x)
                            torch.mps.synchronize()
                        fwd = (time.perf_counter() - t) / 2
                    except Exception as e:
                        log(f"{key:15} window {chunk / SR:4.1f}s fp16={half!s:5} FAILED: {str(e)[:160]}", out)
                        continue
                    loads = "  ".join(f"overlap {o}: {fwd / (chunk / o / SR):5.0%}" for o in OVERLAPS)
                    log(f"{key:15} window {chunk / SR:4.1f}s fp16={half!s:5} forward {fwd:6.2f}s | {loads}", out)
            del sep, inst, model
            torch.mps.empty_cache()
        log("(live needs the percentage well under 100%, ideally < 60%)", out)


if __name__ == "__main__":
    main()
