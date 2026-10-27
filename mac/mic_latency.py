# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "sounddevice"]
# ///
"""How long does your voice take from the mic to the speakers on this Mac, and which block size is the fastest that
still plays cleanly? Picks it for the player (saved in ~/karaoke-bench/player.json as "block").

For each block size it opens the mic and the current output exactly as player.py does (same queue, same echo cost,
CoreAudio buffers set to the block), plays 5 short beeps and listens with the mic: the delay from "sent" to
"heard" plus the time the voice waits in the queue is what a singer hears. Late speaker callbacks (each a click)
are counted; a block size with any is not picked. Takes ~30 s. Turn the mic on, point it at the speakers, keep the
volume moderate and the room quiet. Audio stays in memory; only timings are printed and saved to results.txt.
usage:  uv run mic_latency.py
"""

import collections
import json
import os
import threading
import time

import numpy as np
import sounddevice as sd

import player

SR = player.SR
BLOCKS = (64, 128, 256)
QUIET, BEEPS, GAP = 6.0, 5, 0.7         # s of steady running to count clicks / gaps, then beeps GAP s apart


def beep():
    t = np.arange(int(0.003 * SR)) / SR
    return (0.4 * np.sin(2 * np.pi * 2000 * t) * np.hanning(t.size)).astype(np.float32)


def measure(block):
    p = player.Player.__new__(player.Player)
    p.lock = threading.Lock()
    p.mic_q, p.mic_size, p.mic_peak = collections.deque(), 0, 0.0
    p.mic_margin, p.mic_primed, p.mic_gaps, p.block = 0, False, 0, block
    p.mic_times = collections.deque(maxlen=10)
    echo = player.Echo()
    raw, sent, late, fill = [], [], [0], []
    pending = []
    b = beep()

    def mic_cb(indata, frames, t, status):
        raw.append((time.perf_counter(), indata[:, 0].copy()))
        p.mic_callback(indata, frames, t, status)

    def out_cb(outdata, frames, t, status):
        if status.output_underflow:
            late[0] += 1
        fill.append(p.mic_size)
        v = p.take_mic(frames)
        v = v + echo.process(v) * 0.0          # the player's echo, at its cost but silent
        outdata[:] = v[:, None]
        if pending:
            pending.pop()
            k = min(frames, b.size)
            outdata[:k] += b[:k, None]
            sent.append(time.perf_counter())

    mic_dev = player.pick_mic()
    with sd.InputStream(device=mic_dev, samplerate=SR, blocksize=block, channels=1, dtype="float32", latency="low",
                        callback=mic_cb, **player.CA) as i_s, \
            sd.OutputStream(samplerate=SR, blocksize=block, channels=2, dtype="float32", latency="low",
                            callback=out_cb, **player.CA) as o_s:
        time.sleep(1.0)                     # the queue learns its margin here
        gaps0, late0 = p.mic_gaps, late[0]
        time.sleep(QUIET)
        gaps, lates = p.mic_gaps - gaps0, late[0] - late0
        for _ in range(BEEPS):
            pending.append(1)
            time.sleep(GAP)
        reported = (i_s.latency + o_s.latency) * 1000
    times = np.concatenate([tb - (len(x) - 1 - np.arange(len(x))) / SR for tb, x in raw])
    x = np.concatenate([x for _, x in raw])
    direct = []
    for s in sent:
        w = np.where((times > s) & (times < s + GAP - 0.05))[0]
        if len(w) < b.size:
            continue
        corr = np.abs(np.correlate(x[w], b, mode="valid"))
        k = int(corr.argmax())
        if corr[k] > 8 * (np.median(corr) + 1e-9):
            direct.append((times[w[k]] - s) * 1000)
    queue = float(np.median(fill)) / SR * 1000 if fill else 0.0
    return {"block": block, "reported_ms": reported, "direct_ms": float(np.median(direct)) if len(direct) >= 3 else None,
            "queue_ms": queue, "gaps": gaps, "late": lates, "heard": len(direct)}


def main():
    out_name = sd.query_devices(kind="output")["name"]
    mic_dev = player.pick_mic()
    in_name = sd.query_devices(mic_dev if mic_dev is not None else sd.default.device[0])["name"]
    print(f"output: {out_name} | mic: {in_name}")
    results = []
    for block in BLOCKS:
        print(f"block {block} ({block / SR * 1000:.1f} ms)...", flush=True)
        try:
            r = measure(block)
        except Exception as e:
            print(f"  could not open: {e}")
            continue
        total = r["direct_ms"] + r["queue_ms"] if r["direct_ms"] is not None else None
        r["total_ms"] = total
        results.append(r)
        print(f"  voice delay {total:.0f} ms (to the air and back {r['direct_ms']:.0f} + queue {r['queue_ms']:.0f})"
              if total is not None else f"  beeps not heard clearly ({r['heard']}/{BEEPS}): mic on? louder?",
              f"| clicks {r['late']}, voice gaps {r['gaps']}")
        time.sleep(0.5)
    clean = [r for r in results if r["late"] == 0 and r["gaps"] == 0]
    pick = min(clean, key=lambda r: r["block"]) if clean else None
    line = (f"=== mic latency | out: {out_name} | mic: {in_name} | {time.strftime('%Y-%m-%d %H:%M')} | "
            + " | ".join(f"block {r['block']}: {r['total_ms']:.0f} ms, clicks {r['late']}, gaps {r['gaps']}"
                         if r["total_ms"] is not None else f"block {r['block']}: not heard" for r in results))
    if pick:
        conf = player.load_conf()
        conf["block"] = pick["block"]
        os.makedirs(os.path.dirname(player.CONF), exist_ok=True)
        with open(player.CONF, "w") as f:
            json.dump(conf, f)
        line += f" | PICKED block {pick['block']}"
        print(f"\nThe player will use block {pick['block']} from now on"
              + (f": your voice takes ~{pick['total_ms']:.0f} ms." if pick["total_ms"] is not None else "."))
    else:
        line += " | none clean: the player keeps its default"
        print("\nNo block size played cleanly; the player keeps its default.")
    with open(os.path.join(player.HERE, "results.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    main()
