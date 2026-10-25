# Project Karaoke

Karaoke from Spotify on a Windows PC. A vocal-separation model removes the singer live, while the song plays, so
any track on Spotify becomes a backing track. You get a slider for how much of the original singer to keep, a
key shift, and synced lyrics. Everything runs locally on your own NVIDIA GPU.

- **Vocal slider**: 0% = karaoke, 10% (the default) = a trace of the singer that fills the music out without
  getting in the way, 40% = a guide voice, 100% = the original song.
- **Key shift** in semitones, without changing the tempo.
- **Both remembered per song**: a song comes back at the vocal and key you last sang it with; new songs start at
  10% and the original key. The lyrics screen shows "Vocal 10%", and "Key −2" while a song is shifted.
- **Sound presets**: Flat, Less bass, More bass, Clear, Warm, Party, Room for your voice (dips the band a singer
  sits in, so yours cuts through), Small speakers, Late night. Live, click-free, and never louder than Flat
  (`karaoke/eq.py`).
- **Microphone with karaoke effects**: plug a USB mic into the PC for reverb (three room sizes), echo (time and
  repeats), voice tone presets, a compressor, a noise gate and a limiter, with an input meter (`karaoke/mic.py`).
- **Synced lyrics** in the browser (from LRCLIB), timed to what you actually hear, each line lit 0.5 s early so
  the singer sees it coming (adjustable).
- **Phone remote**: scan a QR code to control the vocal, key, volume and Spotify's play / pause / next / previous.
- **English or Traditional Chinese**: an EN / 中文 switch on the remote and the lyrics screen.
- **The default model removes every voice, backing vocals too**: measured on 25 songs with a known instrumental
  it kept the music cleanest, 17.3 dB against 11.2-11.7 dB for the karaoke models (`tools/benchmark_models.py`).
  Karaoke models that keep the backing vocals are in the menu.

## What it can and cannot do

- It works with the **Spotify desktop app playing on this PC**. Not the Spotify web player, not YouTube or Apple
  Music, and not Spotify playing on another device. You can still pick songs from your phone's Spotify app
  through Spotify Connect, as long as the PC is the device playing.
- **What you hear runs behind Spotify**, ~4.5 s by default (adjustable from ~1.5 s to ~14 s under Delay). The
  model needs to hear a little of the future before it can tell the voice from the music, so it waits for that
  audio to arrive; it is not learning anything. A longer delay removes the voice more cleanly.
- **Songs that roll into the next one play on without a gap.** After a manual skip you wait the delay once. Tip:
  add songs to Spotify's queue instead of skipping, and the waits disappear.
- Removal is very good but not perfect: loud, effect-heavy or doubled vocals can leave a faint trace.
- **The microphone** is a USB mic plugged into the PC, mixed into the speakers at the last moment so it is not
  delayed by the separation (Windows reports ~50 ms). No Bluetooth mics: they arrive far too late.
- Only Spotify goes through the model. Other apps keep playing normally, without the delay.
- Lyrics come from LRCLIB, a community database: most popular songs have synced lyrics, some have plain lyrics
  only, some have none.

## What you need

- **Windows 11** (developed and tested there; Windows 10 21H2 or newer may work).
- **An NVIDIA GPU**, GTX 16xx / RTX 20xx or newer, with a current driver (570 or newer). Older cards and AMD or
  Intel GPUs are not supported. On an RTX 3090 the default model uses 21% of real time at the default delay; `tools/doctor.py
  --speed` measures yours.
- **The Spotify desktop app**, signed in. A free account works.
- **[VB-Audio Virtual Cable](https://vb-audio.com/Cable/)** (free). Spotify plays into it, and the app listens to it.
- **Python 3.11** and **git**, and about **8 GB of disk**: ~5 GB of packages, 0.9 GB for the default model, and
  0.2-1.7 GB more for each other model you try. Keep the folder out of OneDrive.

**Setting it up with an AI coding agent?** Open the folder with it and say "set this up for me". It starts at
[AGENTS.md](AGENTS.md): install ([SETUP.md](SETUP.md), a check after each step), then teach you to use it
([GUIDE.md](GUIDE.md)), then put it on your TV ([docs/living-room.md](docs/living-room.md)).

## Use

The full how-to is [GUIDE.md](GUIDE.md). In short:

Double-click **Project Karaoke** on the desktop. A small window opens and karaoke starts: play a song in Spotify.
**Close the window to stop**: Spotify pauses and goes straight back to your normal speakers.

- The window is the remote: vocal, key, volume, delay, model, and Spotify's own buttons. Keys: ↑/↓ original
  vocal ±10%, ←/→ key, `0` resets the key, `V` switches between 10% and the 40% guide.
- **Lyrics screen** (link in the window) opens the lyrics in your browser, with a QR code for phones on the same
  Wi-Fi. Put it on a second monitor or a TV.
- **Optional always-on helper**: started with Windows, it waits in the background so a phone can start karaoke
  without anyone touching the PC (`http://<pc>:8765/`). Without it, phones work while the window is open.

**"Spotify Normal Audio"**, the other desktop shortcut, is only needed if something crashed and Spotify went
silent. Your keyboard's volume keys stay the main volume.

## Uninstall

1. Run `powershell -ExecutionPolicy Bypass -File tools\uninstall.ps1` with Spotify open: it stops the app, puts
   Spotify back on the speakers, and removes the shortcuts.
2. Delete the project folder.
3. Optional: remove VB-Cable (run `VBCABLE_Setup_x64.exe` as administrator, *Remove Driver*, reboot), and the
   Python entry in Windows Security > Firewall > *Allow an app through firewall*.

## How it works

```
Spotify --(per-app output)--> CABLE Input ==> CABLE Output --> separation --> vocal mix + key --> speakers
                                                                  (GPU)        (live sliders)     or network
```

- `karaoke/approuting.py` sends only Spotify to the cable, as the Windows volume mixer does: Windows'
  AudioPolicyConfig interface, the same route EarTrumpet takes. Everything else stays on your speakers.
- `karaoke/engine.py` runs RoFormer separation (weights fetched by
  [audio-separator](https://github.com/nomadkaraoke/python-audio-separator)). It streams in chunks with
  audio-separator's own overlap-add schedule, so the live result equals a whole-song separation, sample for
  sample (`tests/test_streaming.py`). The chunk length is the delay floor.
- `karaoke/pipeline.py` has four threads: capture, separation, mixer, output. The mixer keeps only ~0.15 s ready
  ahead, so the vocal slider and key react at once even though separation runs seconds behind. Instrumental +
  vocal always sum to the original mix, so 100% is the untouched song.
- Every captured sample is tagged with Spotify's track and position, taken from Windows' media session
  (`karaoke/transport.py`). The tag travels through every buffer, so the lyrics page knows exactly which moment
  of the song is coming out of the speakers, at any delay, after pauses, skips and seeks.
- `karaoke/keyshift.py` shifts key with the Rubber Band R3 engine in real-time mode (~90 ms). At key 0 you hear
  the untouched signal, delayed to match.
- `karaoke/agent.py` serves the pages (`karaoke/web/`) and runs sessions; `karaoke/desk.py` is the desktop icon,
  which runs it for as long as its window is open. Its main thread owns the audio streams, because WASAPI
  streams opened from a worker thread fail here ("WDM-KS -9999").
- `karaoke/mic.py` is the PC's microphone: a low-latency input stream whose voice the speakers' callback adds on
  top of the karaoke, through high-pass, gate, tone, compressor, level, reverb + echo and a limiter, block-wise in
  numpy (~4% of real time). The music can run seconds late because the lyrics follow it; the voice cannot.
- `karaoke/lyrics.py` fetches lyrics from LRCLIB, matching on title and duration, and keeps every answer in
  `lyrics_cache.json`, so a song played again shows its lyrics at once.
- `karaoke/models.py` lists the models with their leaderboard scores.

## Measured facts (on the test hardware: an RTX 3090 PC and an M1 MacBook Air)

- The CABLE Input volume does not scale the audio through the cable (`tools/cable_gain_check.py`). Only the
  Windows default output matters, and it must be your speakers, or the volume keys do nothing.
- Every model in `karaoke/models.py` runs at 16-25% of real time on an RTX 3090 in fp32 at its native window
  (`tools/bench_models.py`); short delays cost more. The table in `karaoke/models.py` shows how quality drops as
  the delay gets shorter (`tools/latency_eval.py`).
- An M1 MacBook Air (8 GB) is ~3x too slow to run separation live (`mac/mac_bench.py`). That is why the Mac
  route below only plays audio and never separates it.

## On the TV

The simplest living-room setup is this PC plugged into the TV with one HDMI cable: the lyrics screen on the TV,
the karaoke through the TV's speakers, phones as the remote. [docs/living-room.md](docs/living-room.md) walks
through it, with the alternatives (a soundbar, casting only the lyrics, a Mac by the TV) and the microphone.

## Optional: playing in another room through a Mac

For a PC that stays in another room, the finished karaoke can stream to a Mac by the TV. The Mac mixes in a
microphone with reverb, shows the lyrics over HDMI, and relays the phone page so guests' phones need nothing
installed. Away from home it reaches the PC over Tailscale, switched on only for the session. Tested with one PC
and one Mac: [docs/mac-route.md](docs/mac-route.md) and [mac/README.txt](mac/README.txt). A PC-only setup can
ignore `mac/` entirely.

## Tests

`.venv\Scripts\python -m pytest tests -q` needs the GPU: some tests run the real model. `tools/doctor.py`
checks the machine without changing anything.

## Notes

- Spotify audio is only ever held in memory while it plays. Nothing is recorded or saved. Use it within
  Spotify's terms for your own listening.
- The pages on port 8765 have no password: anyone on your Wi-Fi can open the remote. Use it on a home network,
  not on public Wi-Fi.
- Model weights are downloaded from their authors on first use, by audio-separator, into `models/`. They are
  not part of this repository, and each has its own license (anvuew's is GPL-3.0; the others state none).
- Lyrics come from [LRCLIB](https://lrclib.net), a free, community-run lyrics database.
- License: MIT, see [LICENSE](LICENSE).
