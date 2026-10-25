# Using Project Karaoke

Everything a new user needs to know, once it is installed ([SETUP.md](SETUP.md)). AI agents: after setup, walk
your human through this page, the parts in order, in plain words, and show each control on their screen. Then
offer [docs/living-room.md](docs/living-room.md) to put it on the TV.

## Every time, before you start

- **The Spotify desktop app is open on this PC and signed in.** Not the web player, not Spotify on a phone or a
  speaker. A free account works; its ads play through the karaoke too.
- **Spotify plays on this PC.** You can pick songs from your phone's Spotify app (Spotify Connect: the speaker
  icon, pick this PC), as long as this PC is the device playing.
- Your speakers, headphones or TV are connected. They are picked inside the app (below), not in Spotify.
- **Spotify's own volume slider at maximum** (bottom right in Spotify). The karaoke is only as loud as what
  Spotify sends it; turn the room down with the Windows volume keys instead.

### Windows sound: what to set, what to leave alone

- **Set:** Windows' output (Settings > System > Sound > *Output*) to your real speakers, headphones or TV, never
  to "CABLE Input". That keeps the volume keys working.
- **Leave alone:** Spotify's own output device in Windows' volume mixer (*App volume and device preferences*). The
  app moves Spotify onto the cable while karaoke runs and back afterwards, by itself. In Spotify, leave the
  playback device on "This computer".
- **Where the karaoke plays** is the app's own choice (*Engine settings > Speakers or headphones*), saved
  separately from Windows'. Plugging in a TV or headphones does not move the karaoke there: pick them in the app.

## Start and stop

- **Start:** double-click **Project Karaoke** on the desktop. A small window opens, loads the model (a few
  seconds; the very first time it says *Downloading the model…* and takes a few minutes), and then says
  *Waiting for Spotify* or *Paused*. Play a song in Spotify.
- **Stop:** close the window. Spotify pauses and goes straight back to your normal speakers, as if the app had
  never run, and the app exits. (A minimised window that is never closed, for example if the PC sleeps, stops
  by itself after about 90 s.)
- **Stop karaoke** (in the window or on the phone) does the same to Spotify but keeps the window open, with the
  model unloaded; **Start karaoke** turns it back on.
- **Spotify Normal Audio**, the other desktop icon, is only for after a crash, if Spotify plays nothing.

## Why there is a wait

What you hear runs about **4.5 seconds behind Spotify**. The model has to hear a little of what comes next before
it can tell the singer from the music, so it waits for that audio to arrive. That wait happens:

- **when a song starts after you skip or pick one, or after you drag the progress bar**: you hear silence for
  the delay (the status pill says *Getting ready*), then the karaoke version;
- **not between songs that follow each other** in a playlist or the queue: they play on without a gap.

So: line songs up in Spotify's **queue** instead of skipping, and the waits disappear. Spotify's own progress
bar and the karaoke's are 4.5 s apart; the karaoke window and lyrics show what you actually hear.

## The controls

The window, the phone page and the keyboard all do the same things. **EN / 中文** at the top switches the window,
the phone page and the lyrics screen between English and Traditional Chinese; each browser remembers its choice,
and a phone set to Chinese starts in Chinese.

| Control | What it does |
|---|---|
| **Original vocal** | How much of the original singer to keep. **Off** = pure karaoke. **10%** (default) = a trace that fills the music out without getting in the way. **Guide 40%** = clearly audible, to learn a song. **Original** = the untouched song. **Remembered per song**, like the key: new songs start at 10%. The lyrics screen shows it at the top ("Vocal 10%"). |
| **Key** | Up or down in semitones, same tempo. **Remembered per song**: set a song to −2 once and it comes back at −2 next time; songs you never changed play at 0. While a song is off its original key, the lyrics screen shows "Key −2" at the top. |
| **Volume** | A trim on top of the Windows volume, which stays the main volume (your keyboard's volume keys). |
| **Sound** | EQ presets: Flat, Less bass, More bass, Clear, Warm, Party, *Room for your voice* (dips where a singer sits, so yours cuts through), Small speakers, Late night. They change at once and never make it louder. |
| **Lyrics timing** | Moves the lyrics earlier (−) or later (+). Default −0.5 s: each line lights up just before it is sung. Saved. |
| **Spotify buttons** | Play / pause, previous, next, shuffle, and a progress bar you can drag to seek. |
| **Engine settings > Delay** | The wait, from "~1.5 s" to "~8 s", or "Longest, cleanest (~14 s or more)". The labels are approximate: the line under the Spotify buttons ("What you hear is N s behind Spotify") gives the exact figure. Longer removes the voice more cleanly; shorter reacts faster. Changing it restarts the engine for a few seconds. |
| **Engine settings > Model** | The default removes every voice, backing vocals too (it measured best). Others keep the backing vocals. A model not used before downloads first. |
| **Engine settings > Speakers or headphones** | Where the karaoke plays: speakers, headphones, the TV over HDMI. Saved. |

Keyboard (only in the PC window, not on phones): **↑ / ↓** original vocal ±10%, **V** switches 10% ↔ 40%,
**← / →** key, **0** back to the original key.

## The microphone

Plug a USB microphone into the PC (a wired, handheld "dynamic" one is best: it hears your voice and little of the
speakers). In the **Microphone** card, pick it from the list. The rest of the card then appears:

| Control | What it does |
|---|---|
| **Level** | How loud your voice is. **Starts at 0 every time karaoke starts**, so nothing squeals by surprise: raise it slowly while singing. 50% leaves the mic as it is, 100% adds 18 dB for quiet mics. |
| **Meter** (under the level) | What the mic hears, before the level. Green is fine; orange means the mic itself is too loud and will distort: lower it in Windows (Settings > System > Sound > Input > the mic > Input volume). |
| **Reverb** + Small room / Room / Hall | The space around your voice, from a small room to a hall. The single most "karaoke" setting; 20-40% is typical. |
| **Echo**, Time, Repeats | The classic karaoke-machine echo: your voice comes back after *Time* (60-500 ms; 150-250 is typical), and *Repeats* sets how many times it fades back. 0% = off (default). |
| **Voice** | Tone presets for the mic: Natural, Warm (fuller, softer top), Bright (more air), Clear (less boxiness, more presence), Deep. |
| **Smooth** | A compressor: soft lines come up, loud ones come down, so the voice sits evenly over the music. On by default. |
| **Noise gate** | Turns the mic down between phrases, so it does not keep picking up the speakers or the room. On by default. |

Under the level, the card shows about how long your voice takes from the mic to the speakers (Windows' own
figure, ~50 ms). A limiter keeps the voice from ever clipping, however loud someone shouts. With the Mac route
(docs/living-room.md option D) the Mac mixes the mic instead, and the card offers its level and reverb only.

- Plugged the mic in while karaoke was on? Press **Stop karaoke** and **Start karaoke** once so the list sees it.
- Settings other than the level are remembered. Pick *Off* in the list to stop using the mic.
- Howling: point the mic away from the speakers, stand beside or behind them, lower the level or the reverb.

## Lyrics screen and phones

- **Lyrics screen ↗** in the window opens the lyrics as a separate page in your normal web browser: the current
  line lit, the next ones below, the song colour behind. Move that browser window to the TV or a second monitor,
  click into it and press **F11** for full screen. The faint 🎨 icon at the top right (it brightens when you point
  at it) changes the colours.
- **A QR code** ("Scan to control") sits in the bottom corner of the lyrics screen, once the window is wider than
  about 760 px. Any phone on the **same Wi-Fi** scans it with its camera and gets the remote. Nothing to install.
  The PC window also shows the address under *Phone remote*.
- **If the code does not open on the phone** (a VPN or virtual network adapter on the PC can make it show the
  wrong address): on the PC run `ipconfig`, take the *IPv4 Address* under the Wi-Fi or Ethernet adapter, and open
  `http://<that address>:8765/` on the phone. The lyrics screen is the same address plus `tv`.
- Lyrics come from LRCLIB, a free community library: most popular songs have timed lyrics, some only plain text
  (shown scrolling), some none ("No lyrics found"; the music still plays without the voice).

## Optional: start it from a phone, with nobody at the PC

The **always-on helper** (offered during setup) waits in the background from Windows start-up. A phone then opens
`http://<PC address>:8765/` (the address as above: shown in the PC window, or found with `ipconfig`; bookmark it)
and presses **Start karaoke**; it also opens Spotify if needed. Without the helper, phones and the lyrics screen
work only while the window is open.

With the helper running, the desktop icon still works: its window simply talks to the helper. Closing that
window stops the karaoke, but the helper keeps waiting for the next start.

## Good to know

- Only Spotify goes through the karaoke. Other apps (YouTube, games, calls) play normally on the speakers.
- Removal is very good, not perfect: loud, effect-heavy or doubled voices can leave a faint trace. A longer
  *Delay* helps.
- Which microphone to buy, and other ways to mix one in: "The microphone" in
  [docs/living-room.md](docs/living-room.md).
- The remote has no password: anyone on your Wi-Fi can open it. Fine at home, not on public Wi-Fi.
- Nothing is recorded. Spotify's audio only passes through memory while it plays.

## When something is off

| You notice | Do this |
|---|---|
| The window says *Waiting for Spotify* | Open the Spotify desktop app on this PC and play a song here (not on the phone or a speaker). |
| You hear the original song, with the singer | The window shows a warning. Quit Spotify completely (tray icon > Quit), open it again. |
| Spotify plays nothing after a crash | Double-click **Spotify Normal Audio**. |
| The volume keys do nothing | Windows' output is set to the virtual cable. Settings > System > Sound > Output: pick your speakers. |
| Stutters or gaps | Close games or other GPU-heavy programs, or pick a longer *Delay*. |
| The phone can't open the remote | The phone must be on the same Wi-Fi (not a guest network), and Windows must treat this Wi-Fi as *Private*. Then try the `ipconfig` address (above). |
| Quieter than Spotify on its own | Spotify's volume slider to maximum. Removing the voice also takes a little loudness out of the song. |
| Lyrics consistently late or early | *Lyrics timing*, one step at a time. |
| No sound at all | *Engine settings > Speakers or headphones*: pick the one you are listening to. |

Anything else: [SETUP.md](SETUP.md) has a longer troubleshooting table, and `agent.log` in the project folder has
the errors.
