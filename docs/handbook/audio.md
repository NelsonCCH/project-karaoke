# Audio core

How the music gets from Spotify to the speakers with the lead vocal removed: capture, separation, mixing, key shift,
EQ, transport, per-app routing and the speaker output. The microphone and the Mac route have their own file
([mic-and-mac.md](mic-and-mac.md)); the app shell, HTTP API and web pages are in [app-and-web.md](app-and-web.md).

Code: `karaoke/engine.py`, `pipeline.py`, `keyshift.py`, `eq.py`, `transport.py`, `approuting.py`, `models.py`,
`winvol.py`, `__init__.py`, `__main__.py`. Tests: `test_streaming`, `test_mixer`, `test_pitch`, `test_eq`,
`test_transport`, `test_seek_tail`, `test_fast_path`. Tools: `bench_models`, `benchmark_models`, `latency_eval`,
`cable_gain_check`, `audio_routing`, `check_devices`, `restore_spotify_audio`, `run_headless`.

**About the numbers.** Measurements below were made on the development PC: an NVIDIA RTX 3090, Realtek onboard
audio, an Audio-Technica ATR2100x USB mic, Windows 11. They are that machine's figures, not yours. Every value that
depends on hardware has a "how to find it on this PC" note, and section 4 collects them.

---

## 1. End-to-end flow

```
Spotify.exe --(per-app route, approuting)--> "CABLE Input"  (VB-Cable render endpoint)
   ==> "CABLE Output" (VB-Cable capture endpoint, WASAPI)   in_sr = the device's default rate
   [PortAudio input callback]  Pipeline._in_cb
   --> in_q  (queue.Queue of (block, captured monotonic time))
   [separation worker thread]  Pipeline._separate_loop / _separate_forever / _process_input
        soxr VHQ in_sr -> 44100, tag every sample (track serial, Spotify position)
        hold (0.3 s), pause / seek / skip handling
        StreamingSeparator.feed (GPU) --> stems SampleBuffer(6): instL instR vocL vocR track pos
   [mixer thread]  Pipeline._mix_loop
        pop MIX_BLOCK = 1024 frames, drift skip, on_track switch, inst + vocal_level * voc,
        _shift (Rubber Band R3, dry delay line), Equalizer, * volume, clip [-1, 1],
        soxr VHQ 44100 -> out_sr --> out SampleBuffer(2)   (or NetSink for the Mac route)
   [PortAudio output callback]  Pipeline._out_cb
        pop frames, count underruns / late / slips, add the mic voice (mic.Mic.pull) last, clip --> speakers
```

### Threads and what each owns

| Thread | Code | Owns / does |
|---|---|---|
| Main thread (the agent's command loop) | `Pipeline.open_streams`, `_open_output`, `set_low_latency`, `fast_path_free`, `stop`; `Session.restart`'s `open_on_main` | Every PortAudio stream open and close. WASAPI streams opened from a worker failed with "Unanticipated host error ... WDM-KS -9999". Model load (`Pipeline.load`) runs on a worker; `Session._on_main` / `call_main` bounce stream work back. |
| PortAudio input callback | `Pipeline._in_cb` | Copies the block and computes `captured = monotonic() - (t.currentTime - t.inputBufferAdcTime)`: the ADC time of the block, not the callback time. Only a `queue.put`. |
| Separation worker (daemon) | `_separate_loop` -> `_separate_forever` -> `_process_input` | Drains all queued blocks at once, resamples (`rs_in`), asks `clock(captured)` for (serial, pos) and tags every sample (`pos + arange/SR`), applies transport flags, feeds the GPU, banks stems. An exception sets `stats["state"] = "failed"` with a status message; out-of-memory gets a "close other GPU programs / shorter delay" hint. |
| Mixer (daemon) | `_mix_loop`, `_shift`, `_reset_mixer` | Keeps `out` filled to `OUT_AHEAD = 0.15 s` (or `sink.ahead`, read live each loop because the Mac's buffer can grow). Owns `shifter`, `dry`, `eq`, `rs_out`, `wet_gain`. Writes `heard_rec`. |
| PortAudio output callback | `_out_cb` | Pops exactly `frames`; zero-fills on shortage; adds the mic at the very end; never blocks except on the `SampleBuffer` lock. |
| Spotify watcher (daemon, own asyncio loop) | `transport.SpotifyWatcher._loop` | Polls Windows' media session every `POLL = 0.1 s`; calls `on_pause` / `on_play` / `on_jump`; holds `_last = (serial, pos, monotonic, playing)` used by `position_at(t)`. |

### Buffers and what each carries

- `in_q`: `(indata.copy()` as `(n, 2)` float32 at `in_sr`, captured monotonic time).
- `hold`, `SampleBuffer(4)`: L, R, track, pos at 44.1 kHz. The last `HOLD = 0.3 s` of tagged input is kept back
  from the separator so a pause can be cut where Spotify actually went silent.
- `tags`, `SampleBuffer(2)`: (track, pos) of the samples *inside* the separator, in feed order. The separator emits
  exactly the samples it was fed, in order, so the oldest `inst.shape[1]` tags belong to the emitted audio
  (`_process_input`). This is how a tag survives the chunk-length GPU delay.
- `stems`, `SampleBuffer(6)`: inst L/R, vocal L/R, track, pos. Tags are stored as float32: the serial is exact for
  integers below 2^24; position has ~1e-4 s resolution at song lengths (the test uses `atol=1e-4`).
- `dry`, `SampleBuffer(2)`: a delay line equal to the Rubber Band latency, so key 0 is the bit-exact delayed input.
- `out`, `SampleBuffer(2)` at `out_sr`, or a `NetSink` (same push/pop/clear/size interface plus `sr`, `ahead`,
  `latency`, `connected`).
- `SampleBuffer` (pipeline.py) is a deque of `(n, ch)` blocks, a `size` and one `threading.Lock`; `pop(n)` returns
  fewer frames only if it is short.

### Clocks

Three independent clocks: VB-Cable's capture clock (`in_sr`), the speakers' clock (`out_sr`), and Spotify's reported
position (media session, extrapolated). Monotonic time joins them.

- **Resampling.** `rs_in = soxr.ResampleStream(in_sr, 44100, VHQ)`, `rs_out = soxr.ResampleStream(44100, out_sr,
  VHQ)`. Both rates come from each device's `default_samplerate` (Windows' "Default Format"). Everything inside is
  44100 (`engine.SR`), the models' training rate.
- **Drift between cable and speakers.** The mixer drops one `MIX_BLOCK` (crossfaded linearly into the next block)
  when `stems.size > prime + step + 1 s`, at most once per `DROP_GAP = 0.5 s` (`stats["dropped"]`). There is no
  speed-up for the opposite direction; a slow cable just eats the bank until an underrun. The skips were made
  crossfaded and rate-limited after a backlog from a Mac stall drained as a burst of cuts that sounded
  fast-forwarded.
- **Heard position.** After every mixed block, `heard_rec = (track, pos of last sample, monotonic, queued)` with
  `queued = dry.size/SR + out.size/out_sr + out_latency` (device latency from `stream.latency`, or `sink.latency`
  reported by the Mac). `heard()` returns `pos - queued + min(elapsed, queued)`: it runs on with the clock but never
  past what was mixed, so it freezes by itself on a pause or underrun. The lyrics follow this, not Spotify's
  position.

### Separation engine (`engine.py`)

- `load_model(key)` builds `audio_separator.Separator(model_file_dir=<repo>/models, ...)`, calls `load_model(file)`
  and returns `(inst.model_run, inst.model_data_cfgdict)`; it rejects non-RoFormer models. Weights download on first
  use (`Pipeline.load` reports "Downloading the model (size, first time only)").
- **ffmpeg is not needed.** `Separator()` normally refuses to start without `ffmpeg` on PATH, which it only needs to
  write audio files. `load_model` sets `Separator.check_ffmpeg_installed = lambda self: None` before building it,
  because the app only borrows the model loader and never writes audio (`mac/mac_bench.py` does the same).
- `StreamingSeparator(key, overlap=4, fp16=False, device="cuda", chunk_seconds=None, model=None)`:
  - `hop = cfg.model.stft_hop_length or cfg.audio.hop_length`; native `chunk = hop * (cfg.inference.dim_t - 1)`;
    with `chunk_seconds`, `chunk = hop * round(chunk_seconds * SR / hop)`. `step = chunk // overlap` (overlap 4,
    *not* the model file's `num_overlap`). Window = Hamming(chunk), on the GPU.
  - Native windows from the model configs: becruily karaoke 441 x 1100 = 485100 samples (11.0 s), Gabox v2 11.0 s,
    Kim 11.0 s (inference `dim_t` 1101), anvuew 512 x 1250 = 640000 (14.5 s), frazer & becruily 512 x 2000 =
    1,024,000 (23.2 s). So "Longest, cleanest (~14 s or more)" is ~14.4 s total for becruily and ~29+ s for frazer.
  - Stem logic: `stems = [target_instrument]` or `training.instruments`; if one is "vocals" (case-insensitive) the
    estimate is the vocal and `inst = mix - est`; otherwise stem 0 is taken as the instrumental. All five current
    models resolve to estimating Vocals (becruily has `target_instrument: null`, instruments [Vocals, Instrumental],
    so index 0). For karaoke models "Vocals" means the lead vocal only, so backing vocals stay in the instrumental.
  - `feed(x)` with `x` shaped `(2, n)`: append to `inp`; while a full chunk is available, run it, accumulate
    `est * window` into `acc` and `window` into `cnt` (GPU tensors), advance `next_start += step`; emit up to
    `next_start` as `acc / cnt`. Latency is exactly one chunk (`latency_samples`; `test_latency_is_one_chunk`).
  - `flush()`: one tail chunk aligned to the end, like audio-separator's offline tail. Tools and tests only; the
    live pipeline never flushes (a jump calls `reset()`).
  - `_trim()` drops consumed buffers only when more than 5 s can go, keeping the last full chunk of input for flush.
  - Residual design: `inst + voc == mix` exactly (atol 1e-6), so the vocal slider at 100% is the untouched song.
- **Pipeline latency** (`Pipeline.latency`) = `(chunk + prime) / SR + HOLD`, with `prime = step + BANK_MARGIN * SR`.
  With overlap 4 that is `1.25 * window + 0.3 + 0.3`, which is how the `models.WINDOWS` labels were derived
  (3 s -> ~4.35 s, labelled "~4.5 s"; 1 s -> ~1.85, "~2 s"; 0.75 -> ~1.54, "~1.5 s").
- Playback starts when `stems.size >= prime` (one hop + 0.3 s margin), because separation emits a whole hop at a
  time. GPU time per chunk on the development PC: ~0.05-0.15 s at short windows, ~0.6 s at the full one.
- `stats["gpu_load"]` = wall time of a feed that ran chunks / audio seconds those hops cover. It must stay well under
  1.0.

### Transport (`transport.py` -> `Pipeline.pause` / `resume` / `jump`)

- The session is the one whose `source_app_user_model_id` contains "spotify" (lower-cased); playing =
  `playback_status == 4`.
- Position = `timeline.position + (now_utc - timeline.last_updated_time)` while playing; Spotify only updates the
  timeline on events.
- **Jump detection.** A title-artist change with more than `NATURAL_END = 4 s` left of the old track -> `on_jump`. A
  change within 4 s of the end is a natural roll-in, *not* a jump, so the old song's tail plays out gaplessly. While
  playing, a position off from the expected one by more than `SEEK_TOLERANCE = 2 s` -> `on_jump`. Play/pause edges
  -> `on_play` / `on_pause`.
- `serial` increments per track change; `tracks[serial]` holds title, artist, album, duration; cover art is cached
  for the last ~5 serials.
- **Pause.** `pause()` sets `paused` and `trim_pause`: the next input block feeds the held 0.3 s only up to the last
  non-silent sample (`SILENT = 1e-5`; Spotify sends digital zeros when paused), then input is ignored. The bank is
  kept, so resume is instant. `resume()` sets `skip_lead`: leading digital silence is dropped so the song joins up
  sample-exact (`test_pause_resume_joins_without_gap_or_loss`).
- **Skip / seek.** `jump()` sets `flush_req`: the worker resets the separator, `hold`, `tags`, `stems`, `out`,
  `heard_rec`, and sets `mix_reset` (the mixer rebuilds the `KeyShifter`, clears `dry`, resets the EQ, makes a new
  `rs_out`, clears `out`). It then gathers `SEEK_LOOK = 0.5 s` of input and `_cut_seek_tail` drops the old song's
  tail. Measured on the cable: Spotify reports the new position 0.10-0.19 s *before* the audio changes, then plays
  ~0.2 s of near silence on a restart. The cut goes at the end of a quiet run (10 ms RMS below `QUIET_DB = -50`)
  within `SEEK_TAIL = 0.3 s`; else at the deepest dip if it is 12 dB below the median; else nothing is cut. The
  mixer also discards a block in flight if `mix_reset` arrived meanwhile.
- **Per-song settings.** `on_track(serial) -> (semitones, vocal)` is called by the mixer when the first sample of a
  new serial reaches it, so a song's saved key and vocal level start exactly with that song even though Spotify
  switched seconds earlier. Implemented in `Session._on_track` (`song_keys` / `song_vocals` in settings.json).
- Other `SpotifyWatcher` uses: `command()` (next, prev, playpause, pause, shuffle, seek through the media session;
  repeat is left out because Spotify accepts the call and ignores it) and `pause_others()` (pauses every non-Spotify
  media session before the speakers are opened for the mic).

### Key shift (`keyshift.py`)

`pylibrb.RubberBandStretcher(SR, 2, PROCESS_REALTIME | ENGINE_FINER (R3) | PitchHighConsistency |
CHANNELS_TOGETHER)`, primed with `get_preferred_start_pad()` zeros; the first `get_start_delay()` output samples are
dropped. The `semitones` setter sets `pitch_scale = 2 ** (s / 12)`. Output comes in variable-sized blocks.

In `Pipeline._shift` the shifter *always* runs; `dry` delays the unshifted signal by exactly the shifter's output
lag. At key 0 the output is the dry signal, bit-exact (`test_key_zero_is_bit_exact_delayed_input`); switching to or
from 0 crossfades over `XFADE = 2048` samples. Latency ~90 ms. `Session` clamps the key to [-12, 12].

### EQ (`eq.py`)

RBJ cookbook biquads as second-order sections. 9 `PRESETS`, `DEFAULT_EQ = "Flat"`. `design()` returns `(sos, gain)`
with `gain = min(1, 1 / peak |H|)` over 10 Hz .. 0.99 x Nyquist: no preset can boost anything (the test enforces
<= +0.01 dB), so "More bass" is not just louder. Flat = `sos None` = a bit-exact copy. On a switch, the new filter is
warmed on the last `WARM = 0.5 s` of input and crossfaded over one block.

Room correction: `room_cuts(f, mag)` derives up to 4 peaking cuts from a `tools/loop_check.py` measurement;
`Equalizer.set_room` adds them on top of any preset. Stored in `room_eq.json` keyed by the speakers' name
(`session.load_room_eq`; git-ignored, per machine). Details in [mic-and-mac.md](mic-and-mac.md). The EQ runs after
the key shift, at 44.1 kHz, before volume and clip.

### Per-app routing (`approuting.py`)

There is no public Windows API for "this app plays on that device". The code uses the internal WinRT factory
`Windows.Media.Internal.AudioPolicyConfig`, IID `ab3d4648-e242-459f-b02f-541c70306324` (Windows 10 21H2 or later),
through its raw vtable: `VTBL_SET = 6 + 19` (SetPersistedDefaultAudioEndpoint), `VTBL_GET = 6 + 20`. The device
string is `\\?\SWD#MMDEVAPI#{0.0.0.00000000}.{guid}#{e6327cad-dcec-4949-ae8a-991e976a79d2}`; empty = Default. It
sets the eConsole and eMultimedia roles, which is what the Volume mixer sets.

- `pids()` runs `tasklist` for `Spotify.exe` (CSV output, `CREATE_NO_WINDOW`); helper processes reject the call
  (OSError, skipped).
- `device_id("CABLE Input")` via pycaw, render endpoints only (`{0.0.0.` prefix).
- `route_spotify(True/False)`; `spotify_on_cable()` -> True / False / None (None = no Spotify process has an audio
  session yet).
- **Windows persists the routing per app.** If the app dies, Spotify stays silent on the cable until restored:
  `tools/restore_spotify_audio.py` (the "Spotify Normal Audio" shortcut); `desk.main` registers `restore_spotify`
  with `atexit`; a running agent restores it when it finds Spotify left on the cable with no engine running.
  `Session.route_check` re-routes every ~2 s while a session runs (Spotify opened later or restarted) and warns
  after 6 s of playing off the cable.

### Output device choice and the low-latency (mic) path

- Input = `find_device("input", "CABLE Output")`: WASAPI host API, case-insensitive substring match, first hit.
- Output default = `default_speakers()`: the WASAPI output whose name equals Windows' default playback device
  (`winvol.default_playback_name`, pycaw `GetSpeakers().FriendlyName`), excluding any name containing "CABLE";
  else the first non-CABLE output. The owner can pick another in the app (setting `speakers`, stored by *name*).
- Without a mic: WASAPI shared, `latency="high"`, blocksize 0.
- With a mic (`low_latency`): try the WDM-KS twin of the same speakers (`ks_twin`): same name before " (" and at
  least one shared non-generic word inside the bracket (`GENERIC_WORDS`), exactly one hit, else None. Opened with
  `latency = KS_LATENCY = 0.005`, tried `KS_TRIES = 8` times 0.25 s apart; else WASAPI `latency="low"` and
  `ks_missed = True` (the session pops a warning naming `winvol.playing_apps()`).
- Before opening for the mic, the session pauses other media players and waits up to `SPEAKER_WAIT = 20 s` until
  `fast_path_free()` (a test WDM-KS open and close on the main thread) succeeds.
- `_out_cb` adds `mic.pull(frames)` after the music, keeps playing it through pauses and skips, and feeds
  `mic.balance`.

---

## 2. Design decisions, with reasons and rejected alternatives

1. **Streaming overlap-add identical to audio-separator's offline demix.** Correctness can be proved numerically:
   live == whole-song separation (`test_stream_matches_offline` < 1e-3 of peak with random block sizes 100-9000;
   `test_reference_schedule_is_audio_separators` checks the reference against the library's own `demix`). Cost:
   latency is one full chunk.
2. **Residual stem** (mix - estimate): inst + vocal == mix exactly, so the vocal slider at 100% is the original.
3. **Shortened analysis windows** (`models.WINDOWS`). Measured with `tools/latency_eval.py` on live songs
   (October 2026), SDR against the full window: 6 s 26-34 dB, 4 s 22-30, 3 s 17-28, 2 s 15-26, 1.5 s 11-16,
   1 s 8-10, 0.75 s 8-10, GPU at 25-40%. The ear decides, not the number: the 1 s window (~2 s total) was fine to
   sing to. Default history: ~6 s -> ~3 s -> ~2 s (by ear) -> ~4.5 s (current `DEFAULT_WINDOW`, ~21% GPU on the
   development PC).
4. **Fixed overheads cut:** `BANK_MARGIN` and `HOLD` from 0.75 / 0.5 s to 0.3 s each.
5. **Rejected: "quality climb"** (tried and reverted, October 2026). Start at a short window, play 3% slow with
   Rubber Band time-stretch while longer windows warm up, then crossfade to them. Removed because the 2 s vs 6 s
   gap measured only 0.3-1 dB on the benchmark, not worth slowed playback and two windows on the GPU. The seek-tail
   fix that came with it stayed.
6. **Model choice** (`models.py`). Candidates were benchmarked with `tools/benchmark_models.py` on 25 MUSDB18-HQ
   test songs (instrumental SDR where backing vocals count as voice, 60 s most-vocal excerpt, 8 s pad): Kim 17.3 dB
   at a 6 s window, the karaoke models 11.2-11.7 dB; Kim won every song. That is expected: Kim removes backing
   vocals too, the karaoke models keep them by design, which is exactly what the metric penalises. Kim became the
   default, then **becruily karaoke won by ear** (October 2026: singing live, it sounded better). Dropped earlier:
   viperx, becruily instrumental, Gabox instrumental; their weights may still sit in an older install's `models/`.
   MVSEP leaderboard scores are in the `models.py` docstring (checked October 2026).
7. **fp32, not fp16.** On the development GPU even the heaviest candidate stayed under 40% of real time in fp32
   (16-25% at the native window). `fp16` exists in the engine and `bench_models.py` measures both, but the app never
   enables it. A slower GPU could try fp16; that needs a code change and a listening check, and the
   `test_streaming` tolerances are for fp32.
8. **Overlap 4 instead of the model file's `num_overlap`** (1 for Kim, 8 for becruily): a fixed step of chunk/4
   makes latency 1.25 x window for every model.
9. **The mixer keeps only 0.15 s ahead**, so vocal level, key and EQ act at once even though separation is seconds
   behind.
10. **Key shift always running, plus a dry delay line:** key changes never cause a gap or jump, and key 0 stays
    bit-exact. **Rejected: pedalboard's streaming PitchShift**, which returned silence when fed block by block
    (keyshift.py docstring), so pylibrb is used directly. pedalboard is still in the requirements.
11. **Sample tagging.** Instead of estimating the delay, every sample carries (serial, position) through all
    buffers, so the lyrics stay right at any delay, after pauses, skips and seeks, and on the Mac route.
12. **Pause keeps the bank:** resume is instant and seamless. A skip or seek flushes; a natural track change is not
    a jump, so endings play out.
13. **Mixer-level reset on a jump.** Resetting only the separator left old audio in the key shifter, dry delay,
    resampler and output buffer, heard as a burst of the old song.
14. **Seek-tail cut**, from the measured shapes in section 1.
15. **Volume is a trim;** Windows' volume on the speakers is the main control. `tools/cable_gain_check.py` showed
    that the CABLE Input endpoint's volume does *not* scale audio through the cable (`winvol.py` docstring), so
    nothing compensates for it. But Windows' default output must be the speakers, or the volume keys do nothing
    (the session warns; doctor FAILs).
16. **Hard clip at +-1.0** after EQ x volume, and again after adding the mic: separation residue or the key shift
    must never produce a burst.
17. **Per-app routing via AudioPolicyConfig** (the technique EarTrumpet uses): only Spotify goes to the cable; other
    apps stay on the speakers without the delay. Applied only while a session runs; restored on stop. Rejected:
    making the cable Windows' default output (the volume keys stop working and everything would be delayed).
18. **Streams on the main thread** (see Traps).
19. **WDM-KS output while a mic is used.** On the development PC's Realtek audio, WASAPI shared output took ~120 ms
    to the air (the driver's sound effects run in it) and mic-to-air was ~170 ms; WDM-KS plus a WASAPI-exclusive mic
    brought it to ~36 ms, and 2 ms speaker blocks to ~28 ms. **Then 2 ms was rejected:** a slip counter matched all
    three audible pops to speaker callbacks 3.4-5.0 ms apart against a ~3 ms buffer. Now `KS_LATENCY = 0.005`, about
    +3 ms, ~30 ms mic-to-air.
20. **Nothing competes with the singer.** A browser tab playing on the same speakers made every WDM-KS open fail,
    and the app silently fell back to ~170 ms. Now: pause other media sessions, 8 retries, wait up to 20 s for a
    test open (a browser paused through media controls held the speakers ~15 s: session idle at 10 s, WDM-KS opens
    at ~13-15 s), then pop a warning naming what still plays.
21. **Free GPU memory on stop.** Dropping the separator left ~3.45 GB on the GPU and as much committed RAM in
    torch's cache; an idle helper was found at 8 GB committed. `stop()` now runs `gc.collect()` +
    `torch.cuda.empty_cache()`: 6.7 -> 3.5 GB committed, 3.45 -> 0 GB GPU, stable across cycles.
22. **Windowless children** (`__init__.py`). Under pythonw, `import torch` runs `ver`, and audio-separator used to
    run `ffmpeg -version` at every model load; each flashed a console window. `__init__` patches
    `subprocess.Popen.__init__` to add `CREATE_NO_WINDOW` when the executable is pythonw.exe. The ffmpeg call no
    longer happens (its check is skipped, see `engine.py`); the patch stays and is harmless.
23. **EQ never boosts, warmed crossfade;** room correction cuts only.

---

## 3. Invariants and traps

### Invariants (tests enforce most)

- `inst + voc == mix` (atol 1e-6) for every separator output (`test_streaming`).
- Streaming output == the offline schedule except the tail chunk (`test_stream_matches_offline`). If you change the
  `feed` / `_trim` / window / step logic, run it.
- The separator emits exactly what it was fed, in order, with a latency of exactly one chunk. The tag alignment in
  `_process_input` (`tags.pop(inst.shape[1])`) depends on this. Any stage that drops, inserts or reorders samples
  must move the tags with them: see how `hold`, `trim_pause`, `skip_lead` and `_cut_seek_tail` all work on the
  4-column tagged array, and how the drift skip crossfades only columns 0-3 and keeps the new block's tags.
- Key 0 is the bit-exact delayed input; key changes never gap (`test_mixer`). Shift accuracy within 3 Hz, level
  kept, latency < 0.5 s (`test_pitch`).
- No EQ preset boosts; Flat is bit-exact; switching is click-free (`test_eq`).
- Pause/resume joins with no gap or loss; tags survive a pause and the separator delay; a jump drops all banked
  audio (`test_transport`); no old audio after a jump (`test_seek_tail`).
- Every PortAudio stream open, close and start happens on the main thread.
- The `stems` column layout (inst L, inst R, voc L, voc R, track, pos) is shared by `_process_input`, `_mix_loop`
  and the tests (`[:, 2:4]` is "vocal").
- Nothing in a callback may allocate heavily or block. `_out_cb` must stay cheap: the mic chain runs there and costs
  ~4% of real time.

### Traps

- **Worker-thread streams.** Opening or starting WASAPI from a worker gave "Unanticipated host error ... WDM-KS
  -9999". `tools/run_headless.py --thread` reproduces exactly this.
- **PortAudio caches the device list at init.** `Session.scan_devices(refresh=True)` calls `sd._terminate();
  sd._initialize()` (private sounddevice API), and only with no stream open. Device indexes change after that:
  never keep an index across a refresh; settings store *names*.
- **Device names differ between host APIs.** On the development PC the same speakers were "Speakers (Realtek(R)
  Audio)" in WASAPI and "Speakers (Realtek HD Audio output)" in WDM-KS. `ks_twin` is a heuristic: on other hardware
  it may return None (then WASAPI "low" is used and, because no twin was tried, `ks_missed` stays False and nothing
  warns) or, in theory, a wrong match. Several hits -> None.
- **WDM-KS is exclusive.** It holds the speakers; it fails while any other program plays on them; a paused browser
  keeps them ~15 s. WDM-KS may not report underflows at all, hence the `slips` counter (callback gap > device
  buffer).
- **WDM-KS opens at the WASAPI device's default rate.** `out_sr` is the WASAPI `default_samplerate`, also used for
  the KS open. If the KS pin does not support that rate, the opens fail and it falls back.
- **`find_device` takes the first substring match** among WASAPI devices. Several VB-Audio products (VB-Cable plus
  the "CABLE-A/B" pack, or Voicemeeter) could match "CABLE Output" ambiguously; `approuting.device_id("CABLE
  Input")` likewise takes the first FriendlyName containing it. Both must point at the same cable pair.
- **The "CABLE" filter.** Every output or input whose name contains "CABLE" (case-sensitive) is hidden from the
  speaker and mic lists. A real device with "CABLE" in its name would vanish.
- **Spotify must be the desktop app** (media session id contains "spotify"; process `Spotify.exe`). The Microsoft
  Store and spotify.com installers both work (doctor checks both). The route applies only to a running process and
  to the process that owns the audio; a Spotify started later is caught by `route_check`. If Windows refuses
  ("Spotify won't move to the cable"), fully quit Spotify (tray > Quit) and reopen it.
- **Silence detection assumes digital zeros** (`SILENT = 1e-5`) while Spotify is paused. Anything else playing into
  CABLE Input (another app routed there by hand, Windows sounds if the cable is the default output) breaks pause
  trimming and seek-tail detection, and gets karaoke-processed.
- **Track changes are detected by the "title - artist" string.** The same song twice in a row, or two tracks with
  identical title and artist, does not increment `serial` (only the seek check fires, if the position jumps back
  more than 2 s). Podcasts and ads show whatever metadata Spotify exposes.
- **Position extrapolation uses `last_updated_time` (UTC wall clock) vs `datetime.now`:** a system clock change
  mid-song causes a false seek (one flush).
- **`clock(captured)` uses the latest poll** (100 ms resolution); the tags carry that error (small; the seek
  tolerance is 2 s).
- **Float32 tags:** fine for positions under ~2 h; the serial is exact to 16.7 M.
- **The mixer reads `self.semitones` / `vocal_level` / `volume` / `eq_preset` without locks** (GIL-atomic attribute
  writes from `Session.apply`). Keep them plain scalars. `on_track` overwrites semitones and vocal at a song boundary
  on the mixer thread.
- **A GPU error kills only the worker:** state "failed", the streams keep running and play silence; the owner must
  Stop and Start. `device="cuda"` is hard-coded with no CPU fallback (CPU would be far too slow anyway).
- **`torch.autocast("cuda")` is entered even with fp16 off** (`enabled=False`): harmless; on a torch build without
  CUDA everything fails earlier anyway.
- **`approuting` depends on an undocumented vtable layout** (19 methods after IInspectable). A Windows update that
  changes the interface would make routing fail with OSError or, worse, call the wrong slot. Factory activation
  raises "AudioPolicyConfig factory unavailable" on Windows before 21H2. Do not touch the vtable indexes without
  re-verifying them against EarTrumpet's current source.
- **`pids()` parses `tasklist` CSV:** the CSV columns are fixed across locales, so it works on non-English Windows.
- **Default output = the cable.** VB-Cable's installer often makes CABLE Input the default playback device. Then
  `default_speakers()` falls back to the first non-CABLE output and the volume keys do nothing (warning in the
  window; doctor FAILs).
- **Model downloads** go to `<repo>/models` through audio-separator: network needed on the first start of each model
  (0.2-1.7 GB). A half-downloaded file shows as "Loading" (only existence is checked) and then fails to load: delete
  it and start again.
- **OneDrive:** keep the project out of OneDrive (README, doctor); the model files are large.
- **Stop order:** `stop()` stops the streams, joins worker and mixer (5 s timeout each), then frees GPU memory. Do not
  drop the `gc.collect()` / `empty_cache()`.

---

## 4. What depends on the PC, and how to find it out

| Assumption | Where | On a different PC | How to discover / check (read-only first) |
|---|---|---|---|
| NVIDIA GPU, CUDA torch build from `requirements.lock.txt` (cu128 wheels), driver >= 570 | engine `device="cuda"` | AMD / Intel / no GPU: unsupported, fails at model load. A smaller GPU: higher `gpu_load`, maybe out of memory at long windows | `tools/doctor.py` (runs a real CUDA op; warns under 6 GB VRAM). `nvidia-smi` for the driver. `tools/doctor.py --speed [model]` times every delay (PASS < 60% load, WARN < 85%, FAIL above); it uses the GPU but plays no audio. `tools/bench_models.py` gives forward time, load and VRAM per model, fp32 and fp16. |
| GPU speed: on the development RTX 3090, 16-25% of real time at the native window, ~21% at the ~4.5 s default, 25-40% at short windows | models.py, README, engine | Load scales inversely with GPU speed; short windows cost more per second of audio (more hops) | Pick a `DEFAULT_WINDOW` / model whose `--speed` load is PASS; watch `stats.gpu_load` in `/state` while playing. |
| VRAM: ~3.45 GB held by a session on the development PC (becruily, default window) | engine / pipeline | 6 GB cards are borderline at long windows | `bench_models.py` VRAM column; the out-of-memory message from `_separate_loop`. |
| VB-Audio Virtual Cable with its default names "CABLE Input" (render) / "CABLE Output" (capture) | `pipeline.find_device`, `approuting.device_id`, `latency_eval`, `cable_gain_check`, `check_devices`, `run_headless`, doctor | Renamed endpoints (renamed in Sound settings, localized names, VB-Cable A/B, Voicemeeter) break the substring match | `tools/check_devices.py` prints the chosen input and output, host API and default rates and opens both for 2 s (plays nothing; the output opens silent). `.venv\Scripts\python -c "import sounddevice as sd; print(sd.query_devices())"` lists every device by host API. `tools/audio_routing.py` lists active playback endpoints and volumes (read-only). |
| Cable and speaker rates: whatever their Default Format is (48000 typical) | `open_streams` uses `default_samplerate` | Any rate works (soxr both ways) | `check_devices.py` prints both. |
| CABLE Input's endpoint volume does not scale audio | `winvol` docstring | Probably true for any VB-Cable install, but measured on one PC only | `tools/cable_gain_check.py` plays a 997 Hz tone *into CABLE Input* (not the speakers) and reads it back; compare with the endpoint dB. It writes audio to the cable, so ask the person first. |
| Speakers: Realtek onboard on the development PC, with a WDM-KS twin; its amp fades in after silence | `ks_twin`, `_open_output` | HDMI / TV, USB DACs, Bluetooth: the twin may not exist or not match (then WASAPI "low", ~120-170 ms mic-to-air on the Realtek; unknown elsewhere). Bluetooth output adds its own large latency (fine for music: lyrics follow `heard()` via `stream.latency`, which may under-report Bluetooth) | `sd.query_devices()`: look for the same base name under "Windows WDM-KS"; in a session, `/state` shows `ks_missed` / mic warnings and `stats.slips`, `stats.late`, `stats.underruns`. |
| WDM-KS 5 ms buffer is stable | `KS_LATENCY` | A slower or busier PC may slip at 5 ms (pops) | Watch `stats.slips` and `slip_times` in `/state` with a mic in use; raise `KS_LATENCY` if they grow. |
| Windows 10 21H2+ for AudioPolicyConfig | `approuting` | Older Windows: routing fails | `.venv\Scripts\python -c "from karaoke import approuting; print(approuting.spotify_on_cable())"` (read-only; None if Spotify is not running). |
| Spotify desktop app exposes a media session whose id contains "spotify" | `transport` | Web player and other apps are not supported | The watcher's `has_session` in `/state`. |
| Python 3.11 venv at `.venv`, pythonw for the windowless app | `__init__.py`, SETUP | | `tools/doctor.py` |
| Model weights in `<repo>/models/` | `engine.MODEL_DIR` | A fresh PC downloads only the chosen model (becruily, 1.7 GB) | List `models/`. |
| `room_eq.json` measured for one room and one set of speakers (git-ignored) | `session.load_room_eq` | Absent -> no room cuts; keyed by speaker name | Never copy one from another PC; measure with `tools/loop_check.py` (plays a sweep: needs the person's OK). See [mic-and-mac.md](mic-and-mac.md). |

### Discovery sequence on a new PC (no audio played)

1. `.venv\Scripts\python tools\doctor.py`: Python, packages, GPU, VB-Cable, default output, Spotify, port.
2. `.venv\Scripts\python -c "import sounddevice as sd; print(sd.query_hostapis()); print(sd.query_devices())"`:
   confirm exactly one WASAPI "CABLE Output" input and one "CABLE Input" output; note the speakers' WASAPI name and
   whether a same-named WDM-KS entry exists. The mic fast path depends on it:
   `from karaoke.pipeline import ks_twin, default_speakers; print(ks_twin(default_speakers()))`.
3. `tools/audio_routing.py`: Windows' default playback device must be the real speakers, not CABLE Input. If it is
   the cable, tell the person where to change it (Settings > System > Sound > Output); do not change it yourself.
4. With the person's OK (it uses the GPU, silently): `tools/doctor.py --speed` to choose the delay and model. If the
   default window FAILs, change `models.DEFAULT_WINDOW` or have the owner choose a shorter delay in the app.
5. With the person present and the volume moderate: start a session, play a song, read `/state` (`stats`: state,
   `gpu_load`, `underruns`, `dropped`, `late`, `slips`), or run `tools/run_headless.py 30` (plays to the speakers at
   volume 0.3; prints stats each second).

---

## 5. Debugging and extending

### Where to look

- `agent.log` (repo root, WARNING level, git-ignored): startup failures, command exceptions, "Spotify was left on
  the cable" restores. pythonw has no console; the log is all there is.
- `GET http://127.0.0.1:8765/state` -> `stats` (`Pipeline.stats` + `slip_times`), `spotify` (serial, position), and
  warnings (routing, default output is the cable, mic / `ks_missed`). Status strings from `on_status`: "Downloading
  the model...", "Loading the model...", "Buffering ~N s...", "Vocal removal failed: ...".

| Symptom | Look at |
|---|---|
| Silence but "Playing" | `gpu_load` (> 1 = GPU too slow -> underruns), `underruns`, and whether Spotify is on the cable (`approuting.spotify_on_cable()`). Spotify on the cable with the app not running = silence: restore it. |
| The original song is heard | Spotify not on the cable (the `route_check` warning), or the vocal slider at 100%. |
| Clicks with a mic | `late` (device reported underflow) and `slips` (callback gap > buffer): raise `KS_LATENCY`. |
| Lyrics drift | `heard()` relies on `out_latency` from PortAudio; Bluetooth and TVs may under-report. The lyrics-offset setting compensates. |
| A fraction of a second of the previous song after a skip | `_cut_seek_tail` thresholds (`QUIET_DB`, `SEEK_TAIL`); re-measure on the cable. |
| Slow drift or a periodic cut | `dropped` rises once per drift skip; frequent = the clocks are far apart, or Mac stalls. |

### Tools

- `tools/check_devices.py`: opens the cable and speakers exactly as the app does (WASAPI shared), reports errors and
  the cable's peak (non-zero = Spotify is reaching the cable). Plays nothing.
- `tools/run_headless.py [seconds] [model] [--thread]`: the pipeline without the UI; `--thread` opens streams from a
  worker to reproduce the -9999 failure. **Plays audio.**
- `tools/latency_eval.py [seconds] [models]`: records from the cable (in memory only, never to disk) while a song
  plays and compares windows / overlaps against the full window (vocal and inst SDR, GPU load, ms per chunk, total
  latency). Re-run when choosing windows on a different GPU.
- `tools/benchmark_models.py DATASET_DIR [models] [windows]`: SDR against known instrumentals (MUSDB18-HQ test
  folders: `mixture.wav` + `vocals.wav`). Re-run before changing the model list or default.
- `tools/bench_models.py [keys]`: speed and VRAM per model, fp32 and fp16.
- `tools/restore_spotify_audio.py`: manual un-route (opens Spotify if needed; shows the result in a message box).

### Tests

`.venv\Scripts\python -m pytest tests -q` from the repo root (tests import `karaoke` from the working directory, and
`test_seek_tail` imports `test_transport` as a module; there is no conftest). A GPU and the `kim-vocals` weights are
needed for `test_streaming` only (it downloads `vocals_mel_band_roformer.ckpt`, 0.9 GB, if absent). `test_transport`,
`test_seek_tail`, `test_mixer`, `test_pitch`, `test_eq` and `test_fast_path` need no devices and no GPU (stand-ins:
`PassSep`, `DelaySep`, `Pipeline.__new__`, `FakeStream`, fake media sessions), but importing `pipeline` imports torch
and sounddevice, and `test_fast_path` imports `session` (winrt, pycaw).

### Extending safely

- **New model:** add it to `models.MODELS` (file name as audio-separator knows it, label, size). It must be a
  RoFormer whose stems include "vocals" (or whose stem 0 is the instrumental). Run `bench_models.py <key>`, then
  `benchmark_models.py` and a listening test; check its native window (the "Longest" latency changes).
- **New delay:** add it to `models.WINDOWS`; label = `1.25 * window + 0.6 s`; check `doctor.py --speed`.
- **New EQ preset:** add it to `eq.PRESETS`; `test_eq` automatically checks no-boost and click-free switching; add a
  "does what it says" assertion.
- **New processing in the mixer:** put it between `_shift` and the clip, keep it block-streaming with internal
  state, reset it in `_reset_mixer`, and add its internal delay to `queued` in `_mix_loop`, or the lyrics go out of
  sync.
- **Anything that changes sample counts before the separator** must carry the tag columns.
- **Any new stream open** goes through the main thread (`Session.call_main` / `_on_main`).
- **Changing `HOLD`, `BANK_MARGIN` or the overlap:** update `Pipeline.latency`, the `models.WINDOWS` labels,
  `latency_eval`'s latency formula, and the docs (README: ~4.5 s default, 1.5-14 s range).

---

## 6. Known issues and open ends

| Issue | Where | Suggested fix |
|---|---|---|
| **Silent fallback to the slow speaker path.** When the speakers have no WDM-KS twin (`ks_twin` returns None), a mic session runs on WASAPI "low" (~120-170 ms mic-to-air on the development PC) and nothing warns: `ks_missed` is set only when a twin existed and failed to open. | `pipeline._open_output`, `session.mic_state` | Set a separate flag (e.g. `ks_absent`) when `low_latency` is wanted and no twin exists, and surface it as a mic warning; have `tools/doctor.py` print the twin for the default speakers. |
| **Ambiguous cable match.** `find_device("input", "CABLE Output")` and `approuting.device_id("CABLE Input")` each take the first substring match, so VB-Cable A/B or Voicemeeter installed alongside could pair the wrong endpoints. | `pipeline.find_device`, `approuting.device_id` | Require an exact name (or exactly one match) and have doctor FAIL on several matches. |
| **No speed-up when the cable runs slow.** Drift is handled only by dropping blocks when the bank grows; a cable clock slower than the speakers eats the bank until an underrun. | `Pipeline._mix_loop` | Not seen in practice; if `underruns` grow slowly during long sessions, add a mirror rule (insert a crossfaded repeat block). |

The speaker setting is not validated when set over the HTTP API; see [app-and-web.md](app-and-web.md).
