"""Streaming separation must equal audio-separator's whole-song separation, and stems must sum to the mix.

Runs on the GPU with a real model; the signal is synthetic (tones + noise) because the check is numerical.
usage: python -m pytest tests -s
"""

import numpy as np
import pytest
import torch

from karaoke.engine import SR, StreamingSeparator


def synthetic_song(seconds, seed=0):
    """Backing chords plus a centred 'singer': a harmonic tone with vibrato, vowel-like formants and syllables."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    backing = sum(0.06 * np.sign(np.sin(2 * np.pi * f * t + p)) for f, p in zip([110, 165, 220], rng.uniform(0, 6, 3)))
    f0 = 260 * 2 ** (np.repeat(rng.choice([0, 2, 4, 5, 7], int(seconds * 2) + 1), SR // 2)[: t.size] / 12)
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.01 * np.sin(2 * np.pi * 5.5 * t))) / SR
    formant = lambda h: np.exp(-(((h * f0 - 700) / 300) ** 2)) + 0.5 * np.exp(-(((h * f0 - 1200) / 400) ** 2))
    voice = sum(formant(h) * np.sin(h * phase) / h for h in range(1, 20))
    voice *= 0.5 * (1 + np.sin(2 * np.pi * 2 * t)) ** 2 * 0.15
    x = np.stack([backing + voice, np.roll(backing, 400) + voice]) + 0.005 * rng.standard_normal((2, t.size))
    return x.astype(np.float32)


@pytest.fixture(scope="module")
def sep():
    return StreamingSeparator("kim-vocals")


def offline(sep, mix):
    """The reference schedule from audio-separator's demix: Hamming-weighted chunks every `step`, tail aligned."""
    n, c = mix.shape[1], sep.chunk
    acc, cnt = np.zeros_like(mix), np.zeros(n, np.float32)
    w = sep.window.cpu().numpy()
    starts = []
    for off in range(0, n, sep.step):
        if off + c >= n:
            starts.append(max(n - c, 0))
            break
        starts.append(off)
    for s in starts:
        est = sep._run(torch.from_numpy(mix[:, s : s + c]).cuda()).cpu().numpy()
        acc[:, s : s + c] += est * w
        cnt[s : s + c] += w
    return acc / np.maximum(cnt, 1e-10)


def test_stream_matches_offline(sep):
    mix = synthetic_song(40)
    ref_voc = offline(sep, mix)

    sep.reset()
    rng = np.random.default_rng(1)
    insts, vocs, pos = [], [], 0
    while pos < mix.shape[1]:
        n = int(rng.integers(100, 9000))   # irregular block sizes, like a real audio callback
        i, v = sep.feed(mix[:, pos : pos + n])
        insts.append(i)
        vocs.append(v)
        pos += n
    i, v = sep.flush()
    inst, voc = np.concatenate(insts + [i], 1), np.concatenate(vocs + [v], 1)

    assert voc.shape == mix.shape
    np.testing.assert_allclose(inst + voc, mix, atol=1e-6)
    # Everything before the offline tail chunk follows the identical schedule.
    body = mix.shape[1] - sep.chunk
    err = np.abs(voc[:, :body] - ref_voc[:, :body]).max()
    peak = np.abs(ref_voc).max()
    print(f"max |stream - offline| = {err:.2e}, vocal estimate peak {peak:.3f}")
    assert peak > 0.05, "model found no vocal in the test signal, so the comparison would be vacuous"
    assert err < 1e-3 * peak


def test_reference_schedule_is_audio_separators(sep):
    """Our `offline` reference must be what audio-separator itself produces for a whole file."""
    import logging, tempfile
    from audio_separator.separator import Separator
    from karaoke.engine import MODEL_DIR
    from karaoke.models import MODELS

    lib = Separator(model_file_dir=MODEL_DIR, output_dir=tempfile.gettempdir(), log_level=logging.WARNING)
    lib.load_model(model_filename=MODELS["kim-vocals"]["file"])
    mix = synthetic_song(30, seed=2)
    lib_voc = lib.model_instance.demix(mix.copy())["vocals"]
    # The library uses the model file's num_overlap (1 for this model); we default to 4, so compare like for like.
    step = sep.step
    sep.step = sep.chunk // lib.model_instance.overlap
    try:
        ours = offline(sep, mix)
    finally:
        sep.step = step
    err = np.abs(lib_voc - ours).max()
    print(f"max |ours - audio-separator| = {err:.2e}")
    assert err < 1e-3 * np.abs(lib_voc).max()


def test_whole_signal_in_one_feed_then_flush(sep):
    """One huge feed (trims buffers) then flush must still cover every sample (latency_eval does this)."""
    mix = synthetic_song(45, seed=3)
    sep.reset()
    i1, v1 = sep.feed(mix)
    i2, v2 = sep.flush()
    inst, voc = np.concatenate([i1, i2], 1), np.concatenate([v1, v2], 1)
    assert voc.shape == mix.shape
    np.testing.assert_allclose(inst + voc, mix, atol=1e-6)
    body = mix.shape[1] - sep.chunk     # the tail chunk is scheduled differently, as in the test above
    np.testing.assert_allclose(voc[:, :body], offline(sep, mix)[:, :body], atol=1e-3 * np.abs(voc).max())


def test_latency_is_one_chunk(sep):
    sep.reset()
    out = 0
    fed = 0
    block = 4410
    while out == 0:
        i, _ = sep.feed(np.zeros((2, block), np.float32))
        fed += block
        out = i.shape[1]
    assert fed >= sep.chunk
    assert fed - sep.chunk < block
