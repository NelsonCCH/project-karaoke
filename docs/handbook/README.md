# Developer handbook

This handbook is for an AI agent (or a developer) who will install, run, troubleshoot, change or extend Project
Karaoke on a PC it has never seen. After reading it you should know the project as well as the people who built
it: not only what each part does, but why it is that way, what was measured, what was tried and thrown out, and
where the traps are.

The owner of the PC you are on is probably not technical. You do the technical work; they listen, sing, click
what you ask them to click, and decide what they like. [AGENTS.md](../../AGENTS.md) has the order of work (install,
teach, set up the room, support) and the rules; this handbook is the depth behind it.

## What the project is, in one paragraph

The Spotify desktop app plays into a virtual audio cable instead of the speakers (only Spotify: Windows' per-app
output). The app captures the cable, runs a RoFormer source-separation model on the NVIDIA GPU a few seconds
behind, and plays the backing track to the real speakers with a live slider for how much of the original singer
to keep, a key shift that keeps the tempo, sound presets, and a USB microphone with karaoke effects mixed in at the
very end (so the voice is not delayed with the music). Every audio sample carries Spotify's track and position
through all the buffers, so a web page can show the lyrics of the exact moment coming out of the speakers. The
pages (a remote for phones and the PC, and a lyrics screen for a TV) are served by a small HTTP server on port
8765. An optional route streams the finished backing to a Mac by the TV.

## Goals and non-goals

- **Any song on Spotify becomes a karaoke track, live.** No downloading, no preparing songs, no other music service.
- **Singing must feel right.** The music may run seconds late; the singer's own voice may not (about 30 ms is the
  target, mic to air). The lyrics follow what is heard, never what Spotify is at.
- **Nothing is recorded.** Spotify's audio is only ever in memory. Measurements of the room use a test sweep, and
  the mic is analysed in memory only. Do not add any feature that writes Spotify audio to disk.
- **Everything runs locally**, on the owner's GPU. The only network calls are LRCLIB (lyrics), model downloads,
  and fonts once at install.
- **Non-goals:** AMD / Intel GPUs, the Spotify web player, Spotify playing on another device, Bluetooth
  microphones, macOS or Linux as the separating machine (an M1 Mac measured ~3x too slow).

## Principles behind the decisions

These run through the whole code base; keep them when you change it.

1. **Measure, then decide; and decide by ear when the ear disagrees with the number.** The default model and the
   default delay were both picked by listening while singing, after benchmarks said otherwise (the benchmark
   winner removes the backing vocals too, which sounded empty). Most constants in the code carry the measurement
   that set them, in a comment.
2. **The commit body records why.** Comments and docs say what was measured and what was rejected. When you change
   something, write down why in the same way, so the next agent does not undo it.
3. **Discover the machine, never assume the developer's.** Device names, sample rates, the driver path, GPU
   speed and the room all differ per PC. The code looks them up at run time (`pipeline.find_device`,
   `ks_twin`, `default_speakers`, the device's own `default_samplerate`); keep it that way.
4. **Never leave the owner's PC broken.** Spotify is routed into the cable only while karaoke runs and is put back
   on every way out, crashes included (`session.restore_spotify`, "Spotify Normal Audio" shortcut, a rescue
   check in the helper). Windows settings are only ever read; a person changes them, guided.
5. **Results the owner must see go on screen as a pop-up**, not only in a log or a small status line.
6. **Tests lock behaviour that was hard to get right**: streaming equals offline separation sample for sample,
   the stems sum back to the mix, the tags survive every buffer, the mic queue never plays a short block.

## Map of the code

| Area | Files | Handbook |
|---|---|---|
| Capture, separation, mixing, key, EQ, Spotify transport, per-app routing, models | `karaoke/engine.py`, `pipeline.py`, `keyshift.py`, `eq.py`, `transport.py`, `approuting.py`, `models.py`, `winvol.py` | [audio.md](audio.md) |
| Microphone, effects, anti-feedback, room correction, voice/music balance; the Mac route | `karaoke/mic.py`, parts of `pipeline.py` / `session.py`, `tools/loop_check.py`, `karaoke/netsink.py`, `mac/` | [mic-and-mac.md](mic-and-mac.md) |
| Process model, HTTP API, settings and files on disk, lyrics, the two pages, styles, install and shortcuts | `karaoke/agent.py`, `desk.py`, `session.py`, `lyrics.py`, `karaoke/web/`, `tools/doctor.py`, `make_shortcuts.ps1`, `uninstall.ps1`, `fetch_fonts.py`, `build_looks.py` | [app-and-web.md](app-and-web.md) |
| User-facing docs | `README.md`, `SETUP.md`, `GUIDE.md`, `docs/living-room.md`, `docs/mac-route.md`, `mac/README.txt` | — |
| Tests | `tests/` (pytest; some load the real model on the GPU, `test_netsink` uses the network loopback) | each file names the tests of its area |

## Reading order

1. The top-level [README.md](../../README.md) ("How it works", "Measured facts").
2. [app-and-web.md](app-and-web.md) sections on the process model and the HTTP API: how a session starts and stops.
3. [audio.md](audio.md): the pipeline, end to end.
4. [mic-and-mac.md](mic-and-mac.md) if the owner has a USB microphone or a Mac by the TV.
5. The "Known issues and open ends" section at the end of each file before you start any change.

## First hour on a new PC (after SETUP.md passes)

- `tools\doctor.py` and `tools\doctor.py --speed`: the GPU's load at each delay. That, not the development PC's
  figures, decides which delays and models this PC can run.
- Start karaoke and read `/state` (`Invoke-RestMethod http://localhost:8765/state`): `options` lists the speakers
  and mics Windows offers, `stats` the counters (`underruns`, `late`, `slips`, `dropped`, `gpu_load`), and with a
  mic `mic.latency_ms` shows which speaker path it got: tens of ms means the fast driver path (WDM-KS), well over
  100 ms the shared one. HDMI, USB and Bluetooth outputs often have no WDM-KS twin, and then the voice is slower
  (about 170 ms on the development PC): tell the owner, and see mic-and-mac.md for the options.
- With a mic, and only with the owner's OK and at a moderate volume: the room measurement
  (`tools\loop_check.py`, see mic-and-mac.md), which also finds where the room booms.
- Keep `agent.log` in mind: the app runs without a console, so its warnings and errors land there.

## The tools, and what each does to the PC

Know this before running one: some play sound, some change Spotify's routing.

| Tool | Sound? | Changes anything? | What for |
|---|---|---|---|
| `tools/doctor.py` | no | no | Is the PC ready; with `--speed`, the GPU's load per delay (loads the model, downloads it the first time). |
| `tools/audio_routing.py` | no | no | Where each app's audio goes (Spotify on the cable or the speakers). |
| `tools/check_devices.py` | no (opens the cable input ~2 s and the speakers ~0.2 s, silent) | no | Whether the devices open the way the app opens them. |
| `tools/loop_check.py` | **yes**: a 5 s sweep on the speakers | only `room_eq.json` with `--save` | The room's response and the room correction. Karaoke stopped, the owner's OK, start at `-40`. |
| `tools/cable_gain_check.py` | plays a tone into the cable (not the speakers) | no | Whether the cable's volume scales the audio. |
| `tools/latency_eval.py` | no | no (records the cable in memory only) | Separation quality at each delay, on what plays. |
| `tools/bench_models.py`, `benchmark_models.py` | no | no | Model speed; model quality against a known instrumental. |
| `tools/run_headless.py` | **yes**: the karaoke on the speakers | no | The engine without the pages. It does not route Spotify to the cable: start the app (or route it) first, or it hears nothing. |
| `tools/restore_spotify_audio.py` | no | **yes**: opens Spotify and puts it back on the speakers | Recovery after a crash (the "Spotify Normal Audio" shortcut runs it). |
| `tools/make_shortcuts.ps1`, `uninstall.ps1` | no | **yes**: shortcuts, the helper in the Startup folder; uninstall also stops the app | Install / remove. |
| `tools/fetch_fonts.py` | no | downloads ~45 MB of fonts | Once at install and after updates. |
| `tools/build_looks.py` | no | rewrites `karaoke/web/look/` | Only with the separate design arsenal (not public); never needed to run or tune the app. |
| `mac/` scripts | **yes** (test beeps, on the Mac) | no | The Mac route; see docs/mac-route.md. |

## History in brief

The project was built in about two weeks of autumn 2026 (dates below are 2026).

- **23 Oct**: the streaming engine (RoFormer separation, vocal slider, key shift); audio streams must be opened on
  the main thread; Spotify routed into the cable only while the app runs; pause / skip / seek followed; every
  sample tagged with Spotify's position; LRCLIB lyrics; the always-on helper, the phone remote and the lyrics
  screen; the Mac route with Tailscale for away-from-home use.
- **24 Oct**: the machine is detected rather than assumed; docs for sharing; a "quality climb" to longer windows
  tried and dropped (it gained under 1 dB); a model benchmark; cutting the old song's tail after a skip or seek;
  sound presets.
- **25 Oct**: per-song key and vocal memory; a USB microphone with karaoke effects; EN / 中文; lyrics cache.
- **26 Oct**: the mic path rebuilt for latency: anti-feedback notches, exclusive 2 ms mic blocks, WDM-KS speakers
  (from ~170 ms to ~30 ms mic-to-air), clock drift followed by resampling, the 5 ms WDM-KS buffer, pausing other
  players so nothing competes with the singer.
- **27 Oct**: becruily karaoke as the default model (by ear); mic presets.
- **Early Nov**: GPU memory freed on stop; a reverb whose combs do not pile up on one note; room correction for
  the mic and the music; ten visual styles for the pages; voice-vs-music balance readings; the style shared by
  every page; click a lyric line to jump there, and scroll the lyrics.

## Working with the owner

- Explain each step in one plain sentence; show on their screen rather than describe.
- Ask before anything they will hear: keep the volume moderate, and warn before a test sweep.
- They decide what sounds and looks right. Offer two or three settings side by side and let them pick by ear or
  eye, rather than asking them to name what they want.
- Windows security and system settings (firewall, drivers, network profile, sound devices) are theirs to change:
  say exactly where to click and wait.
