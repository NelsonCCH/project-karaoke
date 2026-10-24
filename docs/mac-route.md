# The Mac route: karaoke in another room (design notes)

The development setup has the PC in one room and the TV in another. A MacBook by the TV plays the finished
karaoke, mixes in a microphone, and shows the lyrics over HDMI. Guests control it from their phones. The same
MacBook works at a friend's place over Tailscale.

This setup is specific to one home: one Mac, the names learnt from one PC. These notes are for people, or AI
agents, who want to build something like it. They cover what the parts are, why they are built this way, and
which traps cost real time. The code is in `mac/` and in `karaoke/netsink.py`, `karaoke/agent.py` and
`karaoke/web/`.

## Shape

```
 PC (GPU)                                              Mac (by the TV)                     phones
 Spotify -> cable -> separation -> mixer --TCP--> player: backing + mic + echo -> HDMI / speakers
                                    |  ^ acks              |  relay :8765  <---------------  QR on the TV
 helper :8765  <--------------------+--+-------------------+  lyrics page in the browser
```

- **The GPU stays on the PC.** An M1 MacBook Air runs the separation model ~3x slower than real time
  (`mac/mac_bench.py`), so the Mac only plays audio.
- **The microphone never crosses the network.** A singer notices ~20-30 ms of delay on their own voice. The
  backing track can be seconds late, because the lyrics follow it (below). So the voice is mixed on the Mac,
  right next to the output: about 77 ms mic-to-speaker on a MacBook Air with its built-in mic, at a block of
  256 frames. A USB audio interface and wired output get that lower.
- **One TCP stream, no WebRTC/RTP.** The backing has a large latency budget, so plain TCP is the simplest thing
  that works. A lost packet becomes a short delay, never a click.

## The audio stream (`karaoke/netsink.py`, `mac/player.py`)

- **Frames.** The Mac opens `GET /audio[?ahead=s]`. The PC sends frames: `int32 n`, then `n` stereo int16 frames
  at 48 kHz. `n == -1` means "drop your queue" (a skip). `n == 0` is a keep-alive.
- **Acks set the pace.** About 20 times a second the Mac sends back `queued_frames device_latency_s underruns`.
  The PC estimates the Mac's queue as `last ack + sent since - elapsed * rate`, and mixes only while that is
  below `ahead`. So the Mac's playback clock paces the PC's mixer, and the drift between the two machines never
  adds up. No resampling to correct for drift is needed.
- **`ahead` is the whole trade-off.** It sets how fast the sliders react, and how big a network stall it rides
  out. Measured stalls: ~0.5 s about every 50 s on home Wi-Fi (the Mac's radio); over a phone hotspot, about
  every 15 s. Home starts at 0.6 s, away at 2 s. After any stall longer than `ahead`, it grows to the stall +
  0.3 s, up to 5 s.
- **Keep-alive.** After a skip, the PC has nothing to send for as long as the delay (~6 s at "~5.5 s"). The
  player's socket timed out at 5 s, reconnected at the start of every song, and the backlog was then cut away
  block by block, which sounded fast-forwarded. The fix is an empty frame every second while idle.
- **One writer.** The mixer thread pushes audio, and the separation thread sends the skip's flush. Without a
  lock around the socket write, two frames can interleave and the player reads garbage lengths.
- **Drift skips are crossfaded and rate-limited** (`pipeline.py`, one per 0.5 s), so a backlog drains without
  being heard. Before this, a burst of hard cuts sounded like fast-forward.

## Lyrics in sync at any delay

Every captured block is tagged with Spotify's track serial and its position at capture time. The position comes
from Windows' media session, extrapolated from its last update. The tags travel through every buffer and the
separator (which emits exactly the samples it was fed, in order). The mixer records which position it just
mixed and how much audio is still queued behind it: its own buffers plus the Mac's reported queue and device
latency. The lyrics page then shows `position - queued + elapsed`. Pauses, seeks and skips need no special
handling. A one-off ± offset absorbs Spotify's own internal buffer, which cannot be measured from outside.

## The Mac player

- **Separate input and output streams.** The built-in mic and AirPods (or the TV) are different devices, and one
  duplex stream across devices fails. Mic frames go into a small queue, capped at 4 blocks so the voice never
  falls behind, and the output callback picks them up.
- **Follow the Sound menu.** PortAudio reads the device list once, at start-up. The player polls Core Audio's
  default output and input (`AudioObjectGetPropertyData` on the system object, selectors `'dOut'` / `'dIn '`)
  once a second. When either changes, it closes the streams and re-initialises PortAudio
  (`sd._terminate(); sd._initialize()`). That is how AirPods or HDMI picked mid-session start working.
- **Never open a Bluetooth mic.** Opening the AirPods' mic switches them into the low-quality headset profile
  for playback too. If the default input is Bluetooth, the player uses the MacBook's own mic.
- **Detect a blocked mic.** If macOS has not granted Terminal microphone access, the stream opens but delivers
  pure zeros. After 4 s of zeros, the player says where to switch it on.
- **Echo** is a small Schroeder/Freeverb room: 4 combs and 2 allpasses. Every delay is longer than one block, so
  each filter runs block-wise in numpy with no per-sample Python loop.

## Finding the PC

The order is: the saved home address → `NAME.local` → a scan of the /24 for port 8765 → Tailscale.

- **Nothing about the PC is built in.** The first run finds it by the scan. The player then saves its address,
  its Windows name and its Tailscale 100.x address (the helper reports them in `/state`) to
  `~/karaoke-bench/player.json`.
- **Resolve the name once.** On macOS, every request by `NAME.local` tried IPv6 first and waited ~2 s. That made
  the relayed phone page take ~10 s to open, and it dropped to 0.01 s with the IP.
- **Tailscale only while needed.** If the PC is not on this network, the player switches Tailscale on with the
  app's own CLI (`/Applications/Tailscale.app/Contents/MacOS/Tailscale up`), then tries the saved 100.x address
  and the MagicDNS name. However the run ends, it switches Tailscale off and quits the app. If Tailscale was
  already on, it is left alone. The PC has to keep Tailscale running, or nothing can reach it from outside.

## Phones and the relay

- **The Mac relays the pages.** It serves port 8765 itself and forwards everything except `/audio` to the PC.
  Phones on the Mac's Wi-Fi therefore need nothing installed, even when the PC is only reachable over Tailscale.
- **The QR points at the Mac.** The relay rewrites `phone_url` in `/state` to the Mac's own address, and the
  lyrics page draws it as a QR code. The encoder is in `karaoke/web/qr.js`: byte mode, error correction level L,
  versions 1-5, with no library. It was verified by decoding with OpenCV.

## Starting and stopping like a switch

- **Desktop icon.** `run.sh` writes `~/Desktop/Karaoke.command`, pointing at its own folder. It is written
  rather than copied: a copied file that came through a download or cloud sync keeps the quarantine mark, and
  macOS then refuses to open it as possible malware. Sync also drops the executable flag.
- **Closing the window must still stop the PC.** Closing a Terminal window sends SIGHUP, which kills Python
  without running `finally`. The player turns SIGHUP and SIGTERM into a normal exit, so it still posts `/stop`.
  As a safety net, the PC stops a Mac session by itself once no player has been connected for 90 s.
- **If Spotify is closed, the helper opens it** (`spotify:` URI). It routes any Spotify that is not confirmed
  on the cable, because a freshly opened one reports "unknown" until it has played.

## Not done

- Measuring the TV's own audio delay over HDMI (`mac/tv_latency.py`), and warning if Game Mode is off.
- Wireless microphones: UHF or 2.4 GHz digital ones add only 0-8 ms. Bluetooth mics add 100-200 ms: unusable.
- After a manual skip, the wait equals the delay. It cannot be hidden: the karaoke version can only start once
  the separator has heard the start of the new song. Songs that roll into the next one play on without a gap.

## Running it

`mac/README.txt` is the hands-on guide for the development Mac. `bash path/to/mac/run.sh` opens a menu, writes
the Desktop icon, and installs `uv` the first time. The player's dependencies (numpy, sounddevice) are inline
script metadata, so `uv run player.py` needs no other setup.
