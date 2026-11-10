# The microphone, the room, and the Mac route

How a singer's voice reaches the speakers in about 30 ms while the music runs seconds behind; how the room is
measured and corrected; how feedback is caught; and the optional route that plays the karaoke on a Mac in another
room. The music pipeline is in [audio.md](audio.md); the app shell and web pages in [app-and-web.md](app-and-web.md).

Code: `karaoke/mic.py`, the mic and room parts of `karaoke/session.py` and `karaoke/pipeline.py`, `karaoke/eq.py`
(`room_cuts`, `Equalizer.set_room`), `tools/loop_check.py`, `karaoke/netsink.py`, `mac/` (`player.py`, `run.sh`,
`README.txt`, `mic_latency.py`, `tv_latency.py`, `mac_bench.py`), `docs/mac-route.md`, `docs/living-room.md`, the
mic sections of GUIDE.md / SETUP.md / README.md. Tests: `test_mic.py`, `test_netsink.py`, `test_mac_player.py`,
`test_presets.py`.

**About the numbers.** PC measurements were made on the development PC: Realtek onboard audio driving the speakers
and an Audio-Technica ATR2100x USB dynamic mic. Mac measurements were made on an M1 MacBook Air. They are those
machines' figures, not the reader's. Section 4 says how to find each one on the PC in front of you.

The one sentence to remember: **the music may be seconds late (the lyrics follow it); the voice may not be more than
~30 ms late.** Every design choice below follows from that split.

---

## 1. How it works

### 1.1 The PC mic path

```
USB mic --WASAPI exclusive, 2 ms blocks--> Mic._in_cb --> deque (Mic.q)
                                                              |
speakers' output callback (Pipeline._out_cb, WDM-KS 5 ms) ---+--> Mic.pull(frames) --> VocalChain.process
     music = self.out.pop(frames)   (separated, seconds late)        |
     outdata = clip(music + voice)  <--------------------------------+
     Balance.add(music, mic samples, voice, track)
```

- **Where the voice joins:** `Pipeline._out_cb`. The voice is added at the very last moment, inside the speaker
  callback, so none of the separation and mixer buffers (`OUT_AHEAD` 0.15 s, key shift ~90 ms, the separation hop)
  delay it. It also keeps playing through pauses and skips, so the singer can talk between songs.
- **Only when output is "pc".** On the Mac route the PC never opens a mic (`Session._open_mic` returns early); the
  Mac mixes its own.
- **Device lists.** `Session.scan_devices` -> `self.ins` = every WASAPI input whose name does not contain "CABLE", so
  built-in mic arrays, headset mics and webcams all appear; `self.outs` likewise. `refresh=True` (`sd._terminate();
  sd._initialize()`) only runs at `start()`, so **a mic plugged in mid-session is invisible until Stop karaoke /
  Start karaoke.** `set(mic_device=...)` is refused if the name is not in the last scanned list.
- **Mic stream** (`Mic.__init__`): first `sd.InputStream(device, samplerate=pipe.out_sr, channels=1, float32,
  latency=MIC_BLOCK=0.002, blocksize=int(0.002 * sr), WasapiSettings(exclusive=True))`; on `PortAudioError` it falls
  back to shared mode (`latency="low"`, `blocksize=0`, `auto_convert=True`). It opens at the **speakers' default
  sample rate** (`out_sr`), so exclusive mode works only if the mic supports that exact rate; otherwise shared. Only
  **the first channel** is read (`indata[:, 0]`).
- **Speaker stream with a mic:** `Pipeline._open_output` with `low_latency=True` finds the speakers' WDM-KS twin
  (`ks_twin`) and opens it with `latency=KS_LATENCY=0.005`, `blocksize=0`, tried `KS_TRIES=8` times 0.25 s apart;
  else WASAPI shared `latency="low"`. Without a mic: WASAPI `latency="high"`. `ks_missed=True` when it wanted KS and
  fell back.
- **`ks_twin(out_dev)`.** Windows names one endpoint differently in WASAPI and WDM-KS (on the development PC "Speakers
  (Realtek(R) Audio)" vs "Speakers (Realtek HD Audio output)"). Match = the same text before " (" AND at least one
  non-generic word in common inside the bracket (`GENERIC_WORDS` = audio, hd, high, definition, device, output,
  input, usb, 2nd, wave; "(r)" stripped), `max_output_channels >= 2`, and **exactly one hit** (else None -> the
  WASAPI path).
- **Start order with a mic** (`Session.restart` -> `work` -> `open_on_main`): pause other players
  (`watcher.pause_others()`: every Windows media session except Spotify) *before* the model loads; after the load, if
  anything was paused, poll `pipe.fast_path_free()` (a test WDM-KS open on the main thread) every 0.25 s for up to
  `SPEAKER_WAIT = 20 s` (status "Waiting for the paused players to let go of the speakers..."); then open the streams
  on the main thread, apply the room EQ to the music (`pipe.eq.set_room(load_room_eq(speakers))`), `_open_mic`,
  `apply()`.
- **Mic chosen while already running** (`set(mic_device=...)` -> `_open_mic`): if the speakers are not yet low
  latency, `_clear_the_speakers` pauses other players and sleeps only 0.5 s, then `set_low_latency(True)` reopens the
  output (a moment of silence). It does **not** do the 20 s wait, so a just-paused browser can still push it onto the
  slow path, which brings up the warning pop-up. Choosing "Off" calls `set_low_latency(False)` (back to WASAPI
  "high").
- **Threading rule:** every stream open and close runs on the main thread via `call_main`; PortAudio WASAPI opened
  from a worker failed with "Unanticipated host error ... WDM-KS -9999".

### 1.2 Latency budget (development PC)

| Path | Mic-to-air |
|---|---|
| WASAPI shared output (driver "sound effects" run in it) + shared mic, 10 ms blocks | ~170 ms (output alone ~120 ms) |
| WDM-KS out + WASAPI-exclusive mic (first version) | ~36 ms |
| + queue fix + 2 ms exclusive mic blocks | ~35 ms, queue ~11 ms |
| + WDM-KS 2 ms buffer | ~28 ms, queue ~6 ms (but pops, see 2.7) |
| current: WDM-KS 5 ms (`KS_LATENCY`) | ~30 ms (+~3 ms vs 2 ms) |

Other measured pieces: the exclusive mic at 2 ms reports 4 ms latency with callbacks never more than ~6 ms apart,
against 12.7 ms reported and up to 21 ms apart at the mic's default 10 ms blocks. The vocal chain costs ~4% of real
time. Capture-to-playback inside the app adds ~7 ms (VB-Cable loop test); Windows reported ~22 ms each way in shared
mode.

General figures that hold on any setup: a singer notices ~20-30 ms of delay on their own voice. A TV not in Game
Mode adds 50-150 ms; Bluetooth output 150-300 ms; casting adds seconds.

`Session.mic_state()["latency_ms"] = (mic.latency + pipe.out_latency) * 1000 + 6`: PortAudio's *reported* figures
plus a 6 ms processing estimate, not a measurement. It does not include a TV or amplifier, or the acoustic path.

### 1.3 The queue between two clocks: `Mic.pull(n)`

- The mic and the speakers deliver different block sizes (measured 480 in / 240 out, or 96 / 96) on two unrelated
  clocks. **Target fill = `self.block` (largest mic block seen) + `n` (this speaker block) + `self.margin`.**
- Not primed: return silence (run through the chain, so effect tails continue) until `size >= target`.
- A short read (`got < n`) is a gap: unprime, `margin += n` up to `MARGIN_MAX = 0.02 s`, log "gap".
- **Drift.** Every `DRIFT_WINDOW = 0.5 s`, `dev = lowest fill in the window - (target - n)`. If `|dev| > block / 2`,
  it is corrected now and then by reading `n + 1` (or `n - 1`) samples for one speaker block and linearly resampling
  them to `n` (`np.interp`), at most once per `1 / DRIFT_RATE` samples (`DRIFT_RATE = 0.002`, under 4 cents of
  pitch). It never cuts.
- `QUEUE_MAX = 0.1 s`: if the speakers stop pulling, the oldest blocks are dropped ("capped").
- Diagnostics: `Mic.stats` {gaps, drops (drift corrections), dropped (net samples), overflow, capped, mic_late,
  speaker_late}; `Mic.recent` (last 40 timestamped events). A callback more than 8 ms after the previous one is
  logged as `mic_late` / `speaker_late`.

### 1.4 The vocal chain: `VocalChain._chunk`, in `CHUNK = 256` sample pieces

In the order a mixing desk would run it:

1. **Input peak** for the meter (`peak_in`; the page decays it x0.3 per poll).
2. **Muted** (`level <= 0`): only the reverb and echo tails ring out from zeros. Nothing else runs (no
   anti-feedback).
3. **High-pass** 90 Hz, 2nd-order Butterworth, always on.
4. **Noise gate** (`gate_on`): RMS of the chunk after the high-pass, before the level slider, against
   `GATE_OPEN_DB = -48` dBFS. It closes after `GATE_HOLD = 0.25 s` of quiet to `GATE_FLOOR = 0.15` (-16 dB, not a
   mute, so word endings survive); opens within one chunk, closes over ~80 ms; the gain is ramped per sample.
5. **Room correction** cuts (`room_sos`, from `room_eq.json`).
6. **Voice tone** preset (`VOICES`): Natural (none), Warm (low shelf +3 @ 220 Hz, high shelf -3 @ 7 kHz), Bright
   (peak +3 @ 3.5 kHz, high +3 @ 9 kHz), Clear (peak -3 @ 300 Hz, peak +3 @ 3 kHz), Deep (low +4 @ 160 Hz, peak -2 @
   2.5 kHz). RBJ biquads from `eq._biquad`.
7. **Compressor** (`comp_on`, labelled "Smooth"): threshold -24 dB, ratio 3, makeup +4 dB, ~5 ms attack / ~150 ms
   release, chunk-RMS detector.
8. **Level:** `level_gain(v)` = 0 at 0, else `10 ** ((v - 50) * 0.36 / 20)`, so 50 = unity and 100 = +18 dB.
9. **Reverb and echo as sends:** `y + reverb(y) * reverb_mix + echo(y) * echo_mix`.
   - `Reverb`: Freeverb-style, **8 combs** `(1108, 1206, 1343, 1394, 1428, 1457, 1528, 1543)` + 4 allpasses
     `(556, 441, 341, 277)`, lengths scaled from 44.1 kHz, a damped one-pole in each comb loop. `SIZES`: Small room
     (feedback 0.74, damping 0.35), Room (0.84, 0.3), Hall (0.9, 0.2). The wet signal is scaled to match the old
     4-comb loudness.
   - `Echo`: one delay line (0.6 s buffer), `ms` 60-500, `repeat` feedback.
10. **Anti-feedback** `FeedbackSuppressor` (always on; `fb_on` is turned off only in tests).
11. **Limiter:** ceiling `LIMIT = 0.8` (leaves headroom for the music). The gain ramps down to the chunk's need at
    once and back up over ~100 ms, then a hard clip at +-0.8. Counter `limited`.

Then `_out_cb` adds the mono voice to both channels and clips to +-1.

**Settings -> chain** (`Session.apply`): `level = level_gain(mic)`; `reverb_mix = echo / 100 * 0.6`;
`reverb.size(reverb_size)`; `echo_mix = delay_level / 100 * 0.7`; `echo.ms = delay_ms`; `echo.repeat = delay_repeat
/ 100 * 0.8`; `gate_on`, `comp_on`; `set_voice(voice)`. **Naming trap: the setting called `echo` is the REVERB
amount** (kept for compatibility with the Mac player's "echo" slider); the karaoke echo is `delay_level` /
`delay_ms` / `delay_repeat`.

Defaults (`Session.__init__`): mic 0 (**never saved; 0 at the start of every session**), echo (reverb) 30,
reverb_size Room, delay_level 0, delay_ms 220 (clamped 60-500), delay_repeat 35, voice Natural, gate on, comp on,
mic_device "".

### 1.5 Anti-feedback (`FeedbackSuppressor`)

It analyses the voice **after** the notches (a deep-enough notch then stops its own deepening): 4096-point FFT, hop
1024 (~12 Hz bins, every ~21 ms at 48 kHz), only above -55 dBFS mean power. A candidate peak (the top 3 per frame in
120-8000 Hz) must pass all of:

| Test | Constant |
|---|---|
| >= 30 dB over the spectrum's median | `FB_ABOVE_DB` |
| >= 18 dB over its neighbours 4-12 bins away | `FB_NARROW_DB` |
| >= 22 dB over every partner partial a sung note would have (f/3, f/2, 2f/3, 3f/2, 2f) | `FB_HARMONIC_DB` |
| present in 6 of the last 8 analyses (~130 ms of 170) | `FB_PERSIST` / `FB_FRAMES` |
| pitch steady within 0.2% (~3.5 cents) across those; voices wander more even on straight notes | `FB_STABLE` |

Each detection deepens a peaking notch by 3 dB, down to -24 dB (`FB_STEP_DB`, `FB_MAX_DB`), Q 18 (~1/12 octave), at
most 8 notches (`FB_NOTCHES`; the shallowest / longest-quiet one is evicted). A notch holds 20 s after its howl last
appeared, then releases at 1 dB/s (up to ~44 s in all; GUIDE says "about 40 s"). Notches within 1/24 octave merge.
Every cut, and every "voice kept (pitch moved x%)" decision, is logged in `fb.recent` and exposed in `/state` as
`mic.events.anti_feedback`, with the margins it passed by.

Tests: a simulated room with loop gain ~1.2 at 2.3 kHz howls without the suppressor and dies with it, caught within
0.5 s; sung notes with and without vibrato are never notched; held low notes are not notched; notches release.

### 1.6 Room measurement and correction

- **Tool:** `tools/loop_check.py [level dBFS, default -20] [--save]`. Karaoke must be stopped (it reads
  `GET http://127.0.0.1:8765/state` `mic.on` and refuses if the mic is on). It uses the `mic_device` and `speakers`
  from `settings.json` (merged with a running helper's settings), so **a mic must have been picked in the app once**.
  It first records a silent pass for the noise floor, then plays 2 s of silence (`LEAD`: the development PC's
  amplifier faded in after silence and ate the start of the sweep), a 5 s exponential sine sweep 60-8000 Hz at the
  given level, and a 1 s tail. Output goes through the WDM-KS twin first (the path the voice uses), else WASAPI
  shared; the mic is opened shared at 48 kHz. Nothing is written to disk except `room_eq.json` with `--save`. It
  plays a test sweep through the speakers: get the person's OK, and start quiet (`-40`, see section 4).
- **Analysis:** regularised deconvolution -> impulse response, windowed from 200 samples before the direct sound to
  +1.5 s. It prints the speakers-to-mic delay and the peak mic level, then multiplies |H| by the chain at the saved
  settings (`chain_gain`: high-pass, `level_gain(mic or 50)`, voice preset) to get the **loop gain per semitone,
  80-1500 Hz**, listing the top 10 notes with "up to +x dB" (= 1/(1-g)). Notes with SNR under 15 dB are flagged
  "rough". 0 dB howls; -6 dB -> notes come back up to +6 dB louder; -20 dB -> +1 dB.
- **`eq.room_cuts(f, mag, sr)`:** 1/6-octave smoothing on a 1/24-octave grid (80-1000 Hz), normalised to the
  100-1000 Hz median; greedily cut the worst bulge in 100-500 Hz (`ROOM_LO` / `ROOM_HI`), re-evaluating after each
  cut, up to 4 cuts (`ROOM_CUTS`), only bulges >= 3 dB (`ROOM_MIN_DB`), each at most 9 dB deep (`ROOM_MAX_DB`), Q 4
  (~1/3 octave, `ROOM_Q`). **Never a boost**: dips are left alone.
- **`room_eq.json`** (repo root, git-ignored): `{"speakers": <exact WASAPI name>, "measured": "...", "cuts":
  [["peak", Hz, -dB, 4], ...]}`. `session.load_room_eq(speakers)` returns the cuts only if the speakers' name matches
  exactly, else []. Applied (only on output "pc") to the music via `Equalizer.set_room` (added to whatever EQ preset,
  re-designed and crossfaded like a preset change) and to the voice via `VocalChain.set_room` (after the gate, before
  the tone). Delete the file to turn it off. Re-measure after moving the speakers.
- **Where to hold the mic:** where people listen and sing, pointing at the speakers. (The `loop_check` docstring says
  "where the singer stands" for the loop-gain reading and "where people listen" for the correction; normally the
  same spot.)
- **Example measurement on the test setup:** bulges of +7.4 dB at 125-131 Hz and +7.7 dB at 175-185 Hz; saved cuts
  184.9 Hz -7.2 dB and 130.7 Hz -6.1 dB; worst loop gain -18 dB at the tested mic level (no near-feedback ringing).
  The same measurement through WASAPI shared read +8 dB in the bass against +5 through WDM-KS, because the Realtek
  driver's effects boost bass. **Always measure through the path that will be used.**

### 1.7 Voice vs music balance (`mic.Balance`)

Numbers only; no audio is kept. Per 0.1 s (`BAL_BLOCK`) of the speaker callback it measures the energy of the mic
input, of the voice at the speakers, and of the music above 200 Hz (`BAL_HP`; bass does not mask a voice). A block
counts as singing if the input is above -45 dBFS (`BAL_SINGING_DB`) **and** above the music - 15 dB (the room hands
the music back to the mic ~25 dB down, measured). It keeps 1800 s of rows (`BAL_KEEP`) keyed by the track heard
(`heard_rec[0]`).

`/state` `mic.events.balance` = a summary for the track playing (needs at least 10 rows = 1 s): seconds,
voice-minus-music median / loud 10% / quiet 10%, input median / loud 5%, voice loud 5%, music median. A first
reading on the test setup: voice +3.9 dB over the music (median), +10 at the loudest 10%, -7.7 at the quietest 10%;
input -24 dBFS median, -18 at the loudest 5%. Use it to advise a singer, who cannot judge from inside the room:
"your quiet lines sink 8 dB under the music: turn Smooth on, or raise the level".

### 1.8 Mic presets (`Session.preset`)

`PRESET_FIELDS = (mic, echo, reverb_size, delay_level, delay_ms, delay_repeat, voice, gate, comp)`: everything in the
mic card except the device. Stored in settings.json as `mic_presets: [{"name", "values"}]`, names trimmed and capped
at 30 characters (`PRESET_NAME_MAX`). Actions via `POST /preset`: save {name}, apply / update / delete {index},
rename {index, name}. `apply` goes through `set()` (validated and clamped). **Applying a preset sets the mic level**,
so after a fresh start (level 0) one tap can jump straight to a loud level: a feedback risk in a new room.

### 1.9 The Mac route (optional)

```
PC: Spotify -> cable -> separation -> mixer -> NetSink --TCP GET /audio--> Mac player.py: backing + mic + reverb
                                       ^ acks ~20/s "queued latency underruns"    -> CoreAudio default output
PC agent :8765 <--- Mac relay :8765 (forwards all but /audio, rewrites phone_url) <--- phones (QR on the TV page)
```

- **Why:** the PC (with the GPU) may be in another room from the TV. An M1 MacBook Air runs separation ~3x slower
  than real time (`mac/mac_bench.py`), so the Mac only plays. The **mic never crosses the network**; it is mixed on
  the Mac.
- **Stream protocol** (`netsink.py`). The Mac sends `GET /audio[?ahead=s]`; the PC sends frames: `int32 n` then `n`
  stereo int16 frames at 48 kHz; `n == -1` = flush (a skip), `n == 0` = keep-alive (every 1 s while idle). The Mac
  sends text lines `queued_frames device_latency_s [underruns]` every 50 ms. The PC estimates the Mac's queue as
  `last ack + sent since - elapsed * 48000` (`NetSink.size`), and the mixer only mixes while that is below `ahead`:
  **the Mac's clock paces the PC**, so no drift correction is needed. No player connected -> `size = 10 ** 9`
  (`NO_PLAYER`; the mixer waits). One writer at a time (`write_lock`), else frames interleave. Socket timeout 10 s
  on the PC, 15 s on the Mac.
- **`ahead`:** 0.6 s by default on the home network (was 0.3; Wi-Fi hiccups); away (over Tailscale) the Mac asks for
  2 s (`AWAY_BUFFER`); clamped 0.3-5 s. Any ack gap over 0.25 s counts as a stall, and if `gap + 0.3 > ahead`, ahead
  grows to `gap + 0.3`, up to `MAX_AHEAD = 5`. A bigger ahead means sliders and key react later. Measured: ~0.5 s
  stalls about every 50 s on a home Wi-Fi network, every ~15 s on a phone hotspot.
- **Mic on the Mac** (`Player.open_streams`): separate input and output streams (one duplex stream across two
  devices fails), both `blocksize=self.block` (`BLOCK = 128`, or what `mic_latency.py` saved), `latency="low"`,
  `CoreAudioSettings(change_device_parameters=True)` so the hardware buffer follows the block (the default is often
  512 frames, ~11 ms each way). `take_mic` uses the same target-fill queue as the PC (`block + n + margin`, margin
  learnt up to `MIC_MARGIN_MAX = 0.03 s`), but **drift is handled by dropping the excess over target + block** (no
  resampling). Chain: `mic * (mic / 100 * 2.0)` (linear, max +6 dB), plus an `Echo` (4 combs 1116 / 1188 / 1277 /
  1356 + 2 allpasses, feedback 0.84, 4-tap smoothing) at `echo / 100 * 0.6`, then a hard clip. No high-pass, gate,
  compressor, tone, limiter, anti-feedback or room EQ on the Mac. The PC page shows "Microphone (on the Mac)" with
  level and reverb only.
- **Device following:** polls the CoreAudio default output and input (`AudioObjectGetPropertyData`, 'dOut' /
  'dIn ') every 1 s; on a change it closes the streams and runs `sd._terminate(); sd._initialize()`. `pick_mic()`: if
  the default input is AirPods / Bluetooth, use the MacBook's built-in mic instead (opening a Bluetooth mic drops the
  earbuds into headset profile). An output named AirPods / Bluetooth / AirPlay gets a note that it adds 150-250 ms,
  too slow to sing through. A mic that sends pure zeros for 4 s means macOS privacy is blocking it -> a message
  pointing to System Settings > Privacy & Security > Microphone (the person allows it; the agent does not).
- **`mic_latency.py`** (run.sh option 6): for blocks 64 / 128 / 256 it opens mic and output exactly as the player
  does, runs 1 s + 6 s of quiet counting late callbacks and queue gaps, then 5 beeps (3 ms, 2 kHz, amplitude 0.4),
  finds each in the mic by correlation (> 8x median), and reports total = air delay + median queue. It saves the
  smallest block with 0 clicks and 0 gaps to `~/karaoke-bench/player.json` as `"block"`. `tv_latency.py <label>`
  measures the output delay (6 beeps, built-in mic) for Mac speakers vs TV vs TV in Game Mode. Results append to
  `mac/results.txt` (git-ignored). Test-machine result: ~77 ms mic-to-speaker with the MacBook Air's built-in mic at
  block 256.
- **Finding the PC** (`find_pc`): the saved `pc` address -> `NAME.local` -> a /24 scan of port 8765 (64 threads,
  0.4 s connect, the host must answer `/state` with "settings") -> Tailscale. `remember()` stores `{pc, name,
  remote: [100.x ...], block}` in `~/karaoke-bench/player.json`, from the PC's `/state` `addresses` and `hostname`
  (`agent.local_addresses`). The resolved IP is then used for everything: `.local` names waited ~2 s per request
  trying IPv6 first (page open 10.25 s -> 0.01 s).
- **Tailscale:** only if the PC is not found on the local network. Uses
  `/Applications/Tailscale.app/Contents/MacOS/Tailscale` (`status`, then `open -g -a Tailscale` + `up`, up to 10
  tries), then tries the saved 100.x addresses and the lower-case MagicDNS name for 30 s. On exit, if the player
  switched it on, it runs `down` and quits the app; if Tailscale was already on, it is left alone. The PC must keep
  Tailscale running. `is_tailscale(host)` = inside 100.64.0.0/10.
- **Relay** (`start_relay`): a `ThreadingHTTPServer` on 0.0.0.0:8765 on the Mac forwards GET and POST to the PC
  except `/audio` (404), and rewrites `/state.phone_url` to `http://<Mac LAN IP>:8765/` so the TV page's QR code
  points at the Mac: phones on the Mac's Wi-Fi need nothing. If 8765 is busy on the Mac, it falls back to the PC's
  address (at home only).
- **Lifecycle:** `POST /start {"output": "mac"}`, then open `http://localhost:8765/tv`. Stop: Return, closing the
  Terminal window (SIGHUP / SIGTERM -> `sys.exit(0)`, so `finally` posts `/stop`), or Stop on a phone
  (`follow_settings` sees `active` go True -> False and does not post `/stop` again). PC safety net:
  `agent.PLAYER_GONE = 90 s` without a connected player stops a Mac session. On a clean stop the Terminal window
  closes itself (found by its tty).
- **`run.sh`:** installs `uv` if missing (curl install script), writes `~/Desktop/Karaoke.command` fresh each time (a
  copied file from a download or cloud sync keeps the quarantine xattr and loses +x, and macOS refuses it), menu
  options 1-6. Scripts carry inline `# /// script` metadata, so `uv run player.py` needs nothing else. A folder under
  a cloud-synced, Desktop or Documents location needs Terminal to have Full Disk Access ("Operation not
  permitted"); the person grants that in System Settings.

---

## 2. Design decisions, with evidence and rejected alternatives

1. **Voice mixed in the speaker callback, never through the separation.** Separation runs seconds behind; lyrics
   follow the music through sample tags, so the music can be late. The voice cannot.
2. **WDM-KS output + WASAPI-exclusive mic while a mic is on.** WASAPI shared output measured ~120 ms to the air on
   the development PC (driver effects run in it); total ~170 ms -> ~36 ms. Rejected: keeping shared "low". Cost: the
   app owns the speakers; nothing else can play on them.
3. **Low latency only while a mic is in use.** Without a mic, WASAPI "high" (robust; other apps can play).
4. **Queue held at a target fill instead of "play whatever is queued".** The old way: 36 of 601 speaker callbacks
   came up short = constant crackle on the voice only. 2 ms exclusive mic blocks cut the callback spacing from up to
   21 ms to at most 6 ms; queue ~11 ms.
5. **Margin learnt from gaps** (`test_a_late_mic...`): a USB mic sometimes hands over a 10 ms block 21 ms late;
   each gap adds one speaker block of margin, up to 20 ms.
6. **Drift handled by resampling, not cutting.** The development mic ran ~130 ppm fast against the onboard audio.
   The previous trim (2 ms every 10-20 s, crossfaded over 64 samples) was audible as a click on sung notes, matched
   to the log within ~1 s. An even earlier version trimmed after every mic stall (3 cuts a minute). Now the rate
   changes by at most 0.2%, and in simulation at +-130 / +-1000 ppm the waveform never steps beyond its own slope
   (the old code: 15-24x).
7. **WDM-KS buffer 5 ms, not 2 ms.** At 2 ms (~3 ms reported) callbacks sometimes came 3.4-5.0 ms apart; all three
   audible pops matched those slips. Since WDM-KS may never report an underflow, a slip counter was added
   (`stats["slips"]`, `slip_times`: callback gap > device latency). Costs ~3 ms.
8. **Pause other players, and wait for them.** A browser tab playing on the speakers made every WDM-KS open fail ->
   a silent fallback to ~170 ms. Now: pause all media sessions except Spotify, retry KS 8 x 0.25 s, and a pop-up
   naming what still plays (`winvol.playing_apps`). A browser paused via media controls holds the speakers ~15 s
   (session idle at 10 s, KS opens at 13-15 s), so the start waits up to 20 s, but only if something was paused.
9. **Anti-feedback with a pitch-stability test.** Without it, held low notes (~190 Hz) passed all the spectral tests
   and were cut by up to 10 dB live. Howls are pitch-locked by the room and the delay; voices wander.
10. **Limiter ramps instead of per-chunk gain jumps:** per-chunk gain made a click on every loud note.
11. **8-comb reverb with searched delays.** The 4-comb version had three delays near multiples of 86.8 samples ->
    resonances at 553 Hz (C#5) and 260 / 277 Hz standing 15-18 dB proud, ringing ~1.7 s against 1.25 s (a "ringing
    bowl"). The new set stays within ~6 dB of the median in 100-1200 Hz (old 4-comb: 12 dB; Freeverb's own 8 combs:
    9.6 dB). Test: `test_no_sung_note_rings_on_in_the_reverb`.
12. **Room correction = cuts only, 100-500 Hz, at most 4, at most 9 dB, measured through the voice's path.** Low
    notes B2-A3 boomed and a song's bass line (138-175 Hz) hummed. A shared-path measurement over-read the bass by
    3 dB (driver effects), so measure via WDM-KS; the amp fade-in needed the 2 s lead. Keyed by speakers only (the
    same cuts on music and voice). Boosting dips was rejected: room nulls cannot be filled, and it costs headroom.
13. **Balance as numbers, not recordings:** privacy, and the singer cannot judge from inside the room.
14. **Mic level 0 at every start, not saved:** no surprise squeal. Presets deliberately include the level.
15. **Mac route: one TCP stream paced by the Mac's acks; no WebRTC / RTP, no resampling.** The backing has a large
    latency budget, so a lost packet is a delay, not a click. The keep-alive fixed a 5 s socket timeout against ~6 s
    of idle after a skip, which had caused a reconnect and a fast-forward-sounding catch-up. Drift skips in the
    pipeline are crossfaded and limited to one per 0.5 s (`DROP_GAP`).
16. **Mac: nothing about the PC built in.** Earlier versions hard-coded the PC's `.local` name and a synced folder
    path; replaced by scan + remember.
17. **Mac: Tailscale on only while away; a relay on the Mac** so phones need no VPN.
18. **Mac: small CoreAudio buffers and a measured pick:** 128 frames by default, 64 / 128 / 256 measured.
19. **Mac mic queue like the PC's:** the old cap of 4 blocks dropped whole blocks -> a stuttering voice.

---

## 3. Invariants and traps

- **Every delay line must be longer than `CHUNK` = 256** (reverb combs and allpasses, `Echo`'s minimum): the
  block-wise numpy processing reads a whole chunk of the delay buffer at once. Allpass 277 was chosen so it still
  exceeds 256 at 44.1 kHz. Raising `CHUNK` or adding short delays silently breaks the filters.
- **The chain runs inside the speaker callback** (GIL, 5 ms buffer). Anything heavy added to `VocalChain` or
  `Balance` risks callback slips = pops on music and voice. Check `stats.slips` / `late` after any change.
- **Streams open and close only on the main thread** (`call_main`).
- **Exclusive mode.** The mic is held exclusively (other apps, a call app for example, lose it; if another app holds
  it exclusively, the open falls back to shared). WDM-KS holds the speakers: system sounds, games and calls cannot
  play there while karaoke with a mic runs. Whether Windows' volume keys still act on a WDM-KS stream depends on the
  driver and has not been verified; check on the new PC at low volume.
- **The WDM-KS twin may not exist or may not match.** HDMI / TV outputs, USB DACs, docks and Bluetooth may have no
  twin or differently-based names -> `ks_twin` returns None -> the WASAPI "low" path, silently (see Known issues).
  Mic-to-air may then be 100 ms or more. Diagnose with the snippet in section 5.
- **Sample rates.** The mic opens at the speakers' default rate; exclusive mode needs the mic to support it exactly
  (USB mics usually do 44.1 / 48 kHz). WDM-KS opens at `out_sr` too. A mismatch -> shared fallbacks.
- **Mono, first channel only.** A 2-input USB interface with two wireless receivers: the second mic is ignored.
- **Bluetooth mics: never** (100-200+ ms; opening a Bluetooth headset mic also drops its playback to call quality).
  Bluetooth / AirPlay / casting outputs are fine for music and unusable for the voice.
- **Feedback.** A mic pointed at the speakers, laptop and array mics, lavaliers, high reverb or echo all raise loop
  gain. The suppressor handles narrow howls; it cannot fix broadband loop gain near 0 dB. A muted chain (level 0)
  does not run the suppressor.
- **Presets can jump the level** from 0 to the saved value in one tap.
- **`echo` = the reverb amount** (section 1.4).
- **Clocks.** PC mic vs PC speakers drift (~130 ppm on the development PC): handled by resampling. Mac mic vs Mac
  output: handled by dropping (may click now and then on long held notes, by analogy with the PC finding; not
  measured). PC vs Mac: handled by ack pacing.
- **The room correction includes the measuring mic's own response and position.**
- **`loop_check` opens WDM-KS at 48000 Hz;** if the speakers' rate differs, the KS open may fail and it measures the
  shared path instead (bass reads high, like the driver effects). Check that the printed path says "(WDM-KS)".
- **`loop_check` needs `mic_device` set** in settings.json and the app stopped; it refuses if `/state` says the mic
  is on, but cannot see an app instance running on another port.
- **Balance needs a known track** (`heard_rec`); between songs, rows are keyed None and the summary is None.
- **Mac network.** Only a /24 scan (bigger subnets, guest Wi-Fi with client isolation, mesh "AP isolation" fail).
  The PC's firewall must allow Python on Private networks and macOS asks to allow incoming connections (relay) and
  microphone access for Terminal: the person answers those prompts. Port 8765 busy on the Mac -> relay disabled.
  The Tailscale path is hard-coded to the .app.
- **NetSink keeps only one player;** a second `/audio` connection replaces the first.

---

## 4. What depends on the PC, and how to find it out

| Assumption | Development value | On a new PC, how to find out |
|---|---|---|
| Speakers have a WDM-KS twin that matches by name | Realtek onboard | List devices (section 5); check `ks_twin(out_dev)` is not None; watch `/state` `mic.warning`. |
| Driver effects add latency and bass in shared mode | Realtek: ~120 ms, +3 dB bass | Compare the `loop_check` delay via WDM-KS vs shared. |
| Mic supports exclusive mode at the speakers' rate | USB dynamic mic at 48 kHz | `/state` `mic.latency_ms`: tens of ms (~30-40) = the fast path; well over 100 = the shared path. |
| Mic clock drift | ~130 ppm fast | `/state` `mic.events.drops` and `recent` "drift +-n" lines. |
| Mic block timing | 10 ms blocks up to 21 ms late (shared); <= 6 ms apart (exclusive, 2 ms) | `mic_late` events. |
| Room bass bulges | 131 / 185 Hz | `loop_check.py` (without `--save` first). |
| Loop gain | -18 dB worst at the tested level | `loop_check.py` top-10 list. |
| Singing levels | input -24 dBFS median | `/state` `mic.events.balance`. |
| Mac | M1 MacBook Air, built-in mic, ~77 ms at block 256 | `run.sh` options 2, 6, 3-5. |
| Network | home Wi-Fi /24, Tailscale on the PC | The Mac's log line "Found the PC at ...". |

### Discovery procedure on a new PC (read-only first)

1. `tools/doctor.py`, then list WASAPI inputs and outputs and WDM-KS outputs and run `ks_twin` for the chosen speakers
   (snippet in section 5). If None, the mic path will be the slow WASAPI path: tell the person, and prefer speakers
   that have a twin (an onboard analog jack usually does; HDMI / TV often differs).
2. Start karaoke with the mic picked and the level at 0, **headphones on** or Windows volume low. Read
   `GET http://127.0.0.1:8765/state` -> `mic`: `on`, `error`, `warning`, `latency_ms`, `events` (gaps, drops,
   overflow, capped, mic_late, speaker_late, margin, limited, notches, room), and the top-level `stats.slips`,
   `stats.late`. Healthy: `latency_ms` ~30-40, gaps stop after the first seconds, slips ~0.
3. Ask the person to say "one two" while they raise the level slowly; listen for delay. Anything they can hear as an
   echo of themselves is more than ~30 ms: check for a TV not in Game Mode, Bluetooth, casting, or `ks_missed`.
4. Room: with karaoke stopped and the person's OK, run `tools/loop_check.py -40` (quieter than the -20 default; raise
   in 6 dB steps until the "rough" / low signal-to-noise flags disappear), mic at the listening spot pointing at the
   speakers. Only `--save` if bulges of 3 dB or more in 100-500 Hz are reported. Re-run after moving the speakers.
5. True mic-to-air latency has no PC tool (see Known issues). Until one exists, use `loop_check`'s printed
   speakers-to-mic delay as the output half.

### No USB mic at all

- Everything except the mic works (separation, vocal slider, key, lyrics, phones, the Mac route). The mic card stays
  "Off: no microphone on this PC"; the pipeline stays on WASAPI "high" and nothing is held exclusively.
- A built-in laptop mic or webcam mic *will* appear in the list. Usable only for a quick test with headphones: array
  mics often run vendor noise suppression / AGC, may not open exclusively (often 4 channels or fixed rates), sit next
  to the speakers (feedback), and colour any `loop_check` measurement. Do not save a room EQ measured with one unless
  nothing else exists.
- A wired headset (3.5 mm or USB) with headphones is the safest test rig: no acoustic loop at all.
- Never a Bluetooth headset mic.
- What to suggest buying: a **wired USB handheld dynamic cardioid** mic (type examples: Audio-Technica
  ATR2100x-USB, Samson Q2U), plugged straight into the PC. Alternatives: a party / karaoke speaker with its own mic
  input (zero latency, no PC mixing; option 2 in `docs/living-room.md`); for wireless, UHF analog or 2.4 GHz digital
  (<= ~8 ms) receivers into a USB audio interface (the app reads the first channel only).
- A safe first test at moderate volume: headphones first; Windows volume ~20-30%; mic level starting at 0, raised in
  small steps while speaking; mic pointed away from the speakers, singer beside or behind them; reverb at 30% or less
  and echo at 0 until there is no howl; Noise gate on; watch `/state` `mic.events.notches` (any entry = the room
  howled somewhere) and `limited`.

### General latency and equipment notes

- Latency by stage: UHF analog wireless ~0 ms; 2.4 GHz digital wireless ~4-8 ms (inaudible); Bluetooth mic
  100-200+ ms (unusable); PC chain ~5-15 ms with exclusive / low-latency drivers, 20-30+ ms shared; TV speakers over
  HDMI 10-100+ ms (Game Mode cuts it; "surround" or "clear audio" processing can add much more); casting adds
  seconds. Singers notice ~20-30 ms in total.
- Before buying anything: play a click from the PC through the intended speakers or TV, record it with any mic near
  the speaker, and read the delay; repeat with Game Mode on and off and the TV's sound processing off. If the total
  is well over ~30 ms, put the voice on a separate small speaker, or a speaker with its own mic input.
- Two singers: a dual receiver with one XLR out per mic into a 2-input USB interface, so each singer could get their
  own channel (this needs `Mic` extended beyond the first channel). Prefer dry receivers (no built-in echo); avoid
  sets that mix both mics into one USB input, speaker-locked mic sets, mics with built-in speakers, and omni lavalier
  capsules near loud speakers. Wireless frequency rules differ by country; check the local ones. Measure any device's
  latency with a loopback before trusting the spec sheet.

---

## 5. Debugging and extending

**Read-only status:** `curl http://127.0.0.1:8765/state` -> `mic` (section 4), `stats` (`underruns`, `late`,
`slips`, `slip_times`), and for the Mac route `player`, `player_connects`, `player_stalls`, `player_underruns`,
`player_buffer`. `mic.events.recent` and `anti_feedback` are timestamped (unix seconds), so a click or howl the
person heard at a given moment can be matched to an event. This is how every fix in section 2 was found: ask for the
time of the glitch, match it to the log.

**List devices and KS twins (no stream opened):**

```
.venv\Scripts\python -c "import sounddevice as sd; from karaoke.pipeline import wasapi_devices, ks_twin; [print('in ', n) for i,n in wasapi_devices('input')]; [print('out', i, n, '-> KS', ks_twin(i)) for i,n in wasapi_devices('output')]"
```

| Symptom | Likely cause |
|---|---|
| Constant crackle on the voice only | Queue gaps: `events.gaps` rising, `mic_late` spikes; is the mic in shared mode? |
| Clicks every 10-20 s on held notes | Drift handling; check `drops` / `dropped`. |
| Pops on music *and* voice | Speaker callback slips (`stats.slips`, `late`): KS buffer too small for this PC (raise `KS_LATENCY`), a chain too slow, or DPC latency from drivers. |
| Voice late (~100+ ms) | `mic.warning` (`ks_missed`), no KS twin, TV not in Game Mode, Bluetooth / casting output. |
| Held notes getting quieter | Anti-feedback notching the voice: `events.anti_feedback` "howl ... cut" at a sung pitch; tune `FB_STABLE` / `FB_HARMONIC_DB`, re-run `tests/test_mic.py`. |
| Some notes boom | Reverb resonance (all sizes, `test_no_sung_note_rings_on_in_the_reverb`) or the room (`loop_check`). |
| "X is playing on these speakers" pop-up | Another app holds the endpoint; close it, Stop / Start. |
| Mac: problems | The "Mic timing" line once per run (callback p50 / max, queue p50, gaps); "Mic: n gaps" lines; the zero-signal warning = privacy block; PC log "player stalled" lines with the buffer size. |

**Tests (no devices):** `.venv\Scripts\python -m pytest tests/test_mic.py tests/test_mac_player.py
tests/test_presets.py tests/test_netsink.py -q`. `test_mic` covers the level curve, mute, unity, high-pass, the
limiter ceiling and no gain jumps, echo timing, reverb size and no ringing note (A2-F#5, all sizes), gate,
compressor, voice presets, odd and mixed block sizes, late-mic margin learning, drift +-ppm without clicks, the queue
staying live, the closed-loop howl simulation, sung and held notes not notched, notch release, balance, room cuts.
`test_netsink` runs a real socket against `Agent(port=0)` (creates a `Session`: scans devices and starts the Spotify
watcher, opens no audio). `test_mac_player` imports `mac/player.py` by path and simulates CoreAudio's bursty 2 x 256
callbacks.

**Extending:**

- **A new effect in the chain:** add it in `VocalChain._chunk` at the right desk position, keep all delays longer
  than `CHUNK`, add a setting to the `Session.settings` defaults + `set()` validation + `apply()` + `PRESET_FIELDS`
  (if per-preset, in both session.py and control.html) + the control.html card + i18n.js, and a synthetic test.
  Measure its cost; stay well under the 5 ms callback.
- **Two mics:** open `channels=2` (or two `Mic` objects), one `VocalChain` per channel, summed before the limiter;
  the queue and drift logic is per device.

---

## 6. Known issues and open ends

| Issue | Where | Suggested fix |
|---|---|---|
| **Silent fallback to the slow speaker path.** With no WDM-KS twin, a mic session runs on WASAPI "low" (~120-170 ms on the development PC) with no pop-up; `ks_missed` is set only when a twin existed and failed. | `pipeline._open_output`, `session.mic_state` | Flag "low latency wanted, no twin" and show it as a mic warning; have doctor print `ks_twin` for the default speakers. |
| **The Mac player lacks the PC's mic fixes:** drift by dropping instead of resampling, no limiter, high-pass, gate, compressor, tone, anti-feedback or room EQ, and the old 4-comb reverb (Freeverb's first four delays, not checked for the ringing found on the PC). | `mac/player.py` | Port drift resampling, the limiter, high-pass, anti-feedback, the 8-comb reverb and the `level_gain` scale; keep `test_mac_player.py` passing. |
| **PC and Mac mic sliders use different scales.** PC: 50 = unity, 100 = +18 dB (`level_gain`). Mac: 50 = unity, 100 = +6 dB (linear `mic / 100 * 2.0`). The same preset sounds different on the two routes. | `mic.level_gain`, `mac/player.py` | Use `level_gain` on the Mac (part of the port above). |
| **`room_eq.json` matches the speakers' name exactly.** A driver update or a renamed endpoint ("2- Speakers ...") turns the correction off with no message. | `session.load_room_eq` | Log or show "room correction measured for X, not applied to Y"; optionally match on the `ks_twin`-style base name. |
| **Only the mic's first channel is read.** A 2-input interface with two mics loses the second. | `Mic._in_cb` (`indata[:, 0]`) | Open all input channels and give each its own `VocalChain` (see "Two mics"). |
| **No PC tool measures true mic-to-air latency.** The PC figures came from a click-from-the-speakers-into-the-mic harness that is not in the repo; `latency_ms` is only PortAudio's reported figures + 6 ms. | `tools/` | Adapt `mac/mic_latency.py`'s beep + correlation approach to WASAPI-exclusive / WDM-KS on Windows (run with karaoke stopped and the person's OK). |
| **`docs/mac-route.md` describes the old Mac mic queue** ("capped at 4 blocks"); the player now uses the target-fill queue. | `docs/mac-route.md` | Update the paragraph to match `Player.take_mic`. |
| **Picking a mic mid-session does not wait for paused players** (0.5 s instead of up to 20 s), so a just-paused browser can still force the slow path. | `Session._clear_the_speakers` | Reuse the `fast_path_free` polling loop from `restart`. |
