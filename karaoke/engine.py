"""Streaming vocal separation.

Runs a RoFormer model over a live 44.1 kHz stereo stream using exactly the same overlap-add schedule that
audio-separator uses offline (chunks of `chunk_size` samples, hop `chunk_size // overlap`, Hamming-weighted),
so the streamed result matches a whole-song separation sample for sample. The price is latency: a sample can
only be finalised once the last chunk covering it has been computed, i.e. roughly one chunk (~8-11 s) late.
"""

import logging
import os
import tempfile

import numpy as np
import torch
from scipy import signal

from .models import MODELS

SR = 44100
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "models")


def load_model(key):
    """Load a model through audio-separator (downloads weights on first use). Returns (torch module, config)."""
    from audio_separator.separator import Separator

    sep = Separator(model_file_dir=MODEL_DIR, output_dir=tempfile.gettempdir(), log_level=logging.WARNING)
    sep.load_model(model_filename=MODELS[key]["file"])
    inst = sep.model_instance
    if not getattr(inst, "is_roformer", False):
        raise ValueError(f"{key} is not a RoFormer model")
    return inst.model_run, inst.model_data_cfgdict


class StreamingSeparator:
    # fp32 by default: on the RTX 3090 even the heaviest candidate uses <40% of real time at full precision.
    # chunk_seconds shortens the analysis window below the model's trained one (~8-11 s): less context per
    # chunk, but the chunk length is the latency floor. None keeps the model's own window.
    def __init__(self, key, overlap=4, fp16=False, device="cuda", chunk_seconds=None, model=None):
        self.key = key
        self.model, cfg = model or load_model(key)
        self.cfg = cfg
        self.device = torch.device(device)
        self.fp16 = fp16 and self.device.type == "cuda"
        hop = int(getattr(cfg.model, "stft_hop_length", None) or cfg.audio.hop_length)
        self.chunk = hop * (int(cfg.inference.dim_t) - 1)
        if chunk_seconds:
            self.chunk = hop * round(chunk_seconds * SR / hop)
        self.step = self.chunk // overlap
        self.stems = [cfg.training.target_instrument] if cfg.training.target_instrument else list(cfg.training.instruments)
        # Which stem the model estimates directly; the other side is the residual.
        names = [s.lower() for s in self.stems]
        if "vocals" in names:
            self.est_index, self.est_is_vocals = names.index("vocals"), True
        else:
            self.est_index, self.est_is_vocals = 0, False
        self.window = torch.tensor(signal.windows.hamming(self.chunk), dtype=torch.float32, device=self.device)
        self.reset()

    @property
    def latency_samples(self):
        return self.chunk

    def reset(self):
        # All buffers are indexed from absolute stream position `base`.
        self.base = 0
        self.inp = np.zeros((2, 0), np.float32)
        self.acc = torch.zeros((2, 0), device=self.device)
        self.cnt = torch.zeros(0, device=self.device)
        self.next_start = 0
        self.emitted = 0

    def _run(self, part):
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16, enabled=self.fp16):
            x = self.model(part[None])[0]
        x = x.float()
        if x.dim() == 2:
            x = x[None]
        return x[self.est_index]

    def feed(self, x):
        """Append input (2, n) float32 and return finalised (instrumental, vocals), each (2, m), possibly m == 0."""
        self.inp = np.concatenate([self.inp, x.astype(np.float32, copy=False)], axis=1)
        received = self.base + self.inp.shape[1]
        while self.next_start + self.chunk <= received:
            s = self.next_start - self.base
            part = torch.from_numpy(self.inp[:, s : s + self.chunk]).to(self.device)
            est = self._run(part)
            need = s + self.chunk
            if self.acc.shape[1] < need:
                pad = need - self.acc.shape[1]
                self.acc = torch.cat([self.acc, torch.zeros((2, pad), device=self.device)], 1)
                self.cnt = torch.cat([self.cnt, torch.zeros(pad, device=self.device)])
            self.acc[:, s:need] += est * self.window
            self.cnt[s:need] += self.window
            self.next_start += self.step
        return self._emit(self.next_start)

    def flush(self):
        """End of stream: finalise everything received so far (the tail is covered by fewer chunks)."""
        received = self.base + self.inp.shape[1]
        if received > self.next_start:
            # One last chunk aligned to the end, like the offline schedule's tail chunk.
            start = max(received - self.chunk, 0)
            s = start - self.base
            if s >= 0:
                part = torch.from_numpy(self.inp[:, s:]).to(self.device)
                est = self._run(part)[:, : part.shape[1]]
                end = s + part.shape[1]
                if self.acc.shape[1] < end:
                    pad = end - self.acc.shape[1]
                    self.acc = torch.cat([self.acc, torch.zeros((2, pad), device=self.device)], 1)
                    self.cnt = torch.cat([self.cnt, torch.zeros(pad, device=self.device)])
                w = self.window[: part.shape[1]]
                self.acc[:, s:end] += est * w
                self.cnt[s:end] += w
        return self._emit(received)

    def _emit(self, upto):
        n = upto - self.emitted
        if n <= 0:
            return np.zeros((2, 0), np.float32), np.zeros((2, 0), np.float32)
        a = self.emitted - self.base
        est = (self.acc[:, a : a + n] / self.cnt[a : a + n].clamp(min=1e-10)).cpu().numpy()
        mix = self.inp[:, a : a + n]
        self.emitted = upto
        self._trim()
        return (mix - est, est) if self.est_is_vocals else (est, mix - est)

    def _trim(self):
        # Drop everything before the earliest sample still needed (emitted output and the next chunk's input).
        # ...and the last full chunk of input, which flush() needs for the tail chunk.
        keep_from = min(self.emitted, self.next_start, self.base + self.inp.shape[1] - self.chunk)
        d = keep_from - self.base
        if d > SR * 5:
            self.inp = self.inp[:, d:]
            self.acc = self.acc[:, d:]
            self.cnt = self.cnt[d:]
            self.base = keep_from
