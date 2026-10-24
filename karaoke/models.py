"""Separation models offered in the menu. All are open-weight RoFormers fetched by audio-separator into models/.

Each model estimates one stem; the other is always the residual (mix - estimate), so instrumental + vocals == mix
exactly and the vocal slider at 100% gives back the original song.

Karaoke models remove the lead singer and keep backing vocals; scores are MVSEP's lead/back vocals leaderboard
(lead vocal SDR / backing + instrumental SDR, full window), checked 2026-10-24:
  anvuew BS 10.23 / 15.61     frazer & becruily BS 10.06 / 15.45
  Gabox MelBand v2 9.77 / 15.16     becruily MelBand 9.61 / 15.00
Speed on an RTX 3090 at the native window, fp32 (tools/bench_models.py): 16-25% of real time for all four.
"""

MODELS = {
    "becruily-karaoke": {
        "file": "mel_band_roformer_karaoke_becruily.ckpt",
        "label": "Karaoke: becruily (recommended)",
        "size": "1.7 GB",
    },
    "anvuew-karaoke": {
        "file": "bs_roformer_karaoke_anvuew.ckpt",
        "label": "Karaoke: anvuew (small download)",
        "size": "0.2 GB",
    },
    "frazer-becruily-karaoke": {
        "file": "bs_roformer_karaoke_frazer_becruily.ckpt",
        "label": "Karaoke: frazer & becruily (fullest backing)",
        "size": "0.2 GB",
    },
    "gabox-karaoke-v2": {
        "file": "mel_band_roformer_karaoke_gabox_v2.ckpt",
        "label": "Karaoke: Gabox v2 (least vocal bleed)",
        "size": "0.9 GB",
    },
    "kim-vocals": {
        "file": "vocals_mel_band_roformer.ckpt",
        "label": "All vocals removed, backing vocals too: Kim",
        "size": "0.9 GB",
    },
}

DEFAULT_MODEL = "becruily-karaoke"

# Analysis window in seconds (None = the model's own ~11 s). The window is the latency floor; total latency is
# window * 1.25 + BANK_MARGIN + HOLD (pipeline.py). SDR against the full window, tools/latency_eval.py, live
# songs 2026-10-23: 6 s 26-34 dB, 4 s 22-30, 3 s 17-28, 2 s 15-26, 1.5 s 11-16, 1 s 8-10, 0.75 s 8-10, with
# the GPU at 25-40%. SDR falls steadily, but by ear the 1 s window (~2 s total) was fine for singing along, so
# the ear decides, not the number.
WINDOWS = {
    "Longest, cleanest (~14 s or more)": None,
    "~8 s": 6,
    "~5.5 s": 4,
    "~4.5 s": 3,
    "~3 s": 2,
    "~2.5 s": 1.5,
    "~2 s": 1,
    "~1.5 s": 0.75,
}
DEFAULT_WINDOW = "~2 s"
