# Karaoke in the living room: ways to set it up

For the AI agent helping someone set this up: read this with your human after [SETUP.md](../SETUP.md) works at
the desk. Ask what they have (a TV with HDMI, a soundbar, a Mac, a microphone, how far the PC is from the TV),
recommend one option below, walk them through it, then do the test at the end. Start with option A unless
something rules it out. Do not change Windows settings yourself: tell the human where to click.

If the PC cannot be brought to the TV (a desktop in another room), the supported ways are a long HDMI cable
(passive cables work to about 10 m; "active" or fibre HDMI cables go further), option C for the lyrics with the
sound on speakers near the PC, or option D if they have a Mac. There is no other way to send the sound to
another room without a delay that ruins singing.

Three things have to reach the room: **the sound** (the karaoke from the PC), **the lyrics** (a web page), and
**the singer's voice** (a microphone, mixed into the sound). The phone remote works in every option.

## A. The PC itself, plugged into the TV with HDMI (simplest; start here)

Good for a gaming laptop with an NVIDIA GPU, or a desktop PC near the TV. One HDMI cable carries both the
picture (lyrics) and the sound (karaoke) to the TV.

1. **Cable.** Plug an HDMI cable from the PC into the TV, and pick that HDMI input on the TV's remote. Many
   laptops have only USB-C: a "USB-C to HDMI" cable or adapter works if the port supports video (a small screen
   or DisplayPort logo next to it).
2. **Picture.** Press **Win+P** on the PC and pick **Extend** (the lyrics go on the TV, the desktop stays on the
   laptop) or **Duplicate** (the TV shows the same as the laptop; simplest to understand).
3. **Start karaoke** with the Project Karaoke icon, as usual.
4. **Sound to the TV.** In the karaoke window: *Engine settings > Speakers or headphones*, pick the TV (it is
   listed by its name, or as something like "NVIDIA High Definition Audio" / "Intel Display Audio"). The choice
   is saved. To use the laptop's own speakers again, pick them there.
5. **Lyrics on the TV.** Click **Lyrics screen ↗** in the karaoke window. With *Extend*, drag that browser
   window onto the TV. Press **F11** for full screen (F11 again leaves it).
6. **Phones.** Everyone on the same Wi-Fi scans the QR code in the corner of the lyrics screen: that is the
   remote (vocal, key, volume, Spotify's play / pause / next). Nobody needs to touch the PC again.
7. **TV picture mode.** Turn on the TV's **Game mode** (sometimes "PC mode" or "Low latency"). TVs delay their
   own sound and picture by 50-150 ms in other modes. The music itself can be late without harm, but the
   microphone's sound through the TV gets late too, and a singer notices that.

Volume: the Windows volume keys (or the TV's remote) are the main volume. The karaoke window's Volume slider is
only a trim on top.

## B. Sound through a soundbar or speakers

- **A soundbar on the TV (HDMI ARC / optical):** set up option A; the TV passes the sound to the soundbar. Nothing
  changes on the PC.
- **Speakers wired to the PC (3.5 mm, USB):** pick them under *Speakers or headphones*; the TV only shows the
  lyrics.
- **A Bluetooth speaker:** fine for the music: pick it under *Speakers or headphones*. Bluetooth adds about
  0.2-0.3 s, so if the lyrics feel late, nudge *Lyrics timing* a step earlier. **Never put a microphone through
  Bluetooth**: the voice comes out 0.1-0.2 s late, which makes singing impossible.

## C. Lyrics on the TV without a cable (the sound stays on the PC's speakers)

The lyrics screen is a plain web page at `http://<PC address>:8765/tv` (the karaoke window shows the address
under *Phone remote*; add `tv` to the end). Any screen on the same Wi-Fi can open it:

- **A smart TV's own web browser**: type the address once and bookmark it.
- **Chromecast / Google TV**: open the address in Chrome on the PC, then Chrome menu > *Cast* > *Cast tab*.
- **Apple TV**: open the address on an iPhone or Mac, then *Screen Mirroring*.
- **Fire TV**: the Silk browser, same address.

The page works only while karaoke's window (or the always-on helper) is running. The PC's address can change
after a router restart: if the bookmark stops working, find the new one with `ipconfig` (GUIDE.md), or ask the
router to always give this PC the same address ("DHCP reservation" in the router's settings; the human does
this).

Casting the picture this way is fine, because the lyrics follow what is heard. Casting the **sound** is not:
casting adds seconds of delay to the voice. Keep the sound on a wired or HDMI output and cast only the picture.

## D. A Mac by the TV, the PC in another room

The PC keeps doing the work and streams the finished karaoke to a Mac, which plays it, mixes the microphone, and
shows the lyrics over HDMI. More parts, but the PC can stay in another room. See
[docs/mac-route.md](mac-route.md) and [mac/README.txt](../mac/README.txt). It needs the always-on helper on the
PC (SETUP.md step 5).

## The microphone

The voice has to be mixed into the karaoke somewhere. The ways to do that:

1. **Into the PC (the usual way).** Plug a USB microphone into the PC and pick it in the karaoke window's
   **Microphone** card. The PC mixes your voice into the karaoke right at the speakers, so it is not delayed by
   the separation. The card has everything a karaoke machine has: level (starts at 0 every time), an input meter,
   reverb with three room sizes, echo with its time and repeats, voice tone presets, *Smooth* (a compressor that
   evens out loud and soft lines) and *Noise gate* (quiets the mic between phrases); a limiter stops it from ever
   clipping. All of it is in [GUIDE.md](../GUIDE.md) "The microphone". The PC's own processing adds about 7 ms;
   Windows' audio buffers add more (the card shows the total it reports, ~50 ms), and a TV adds its own on top
   (use Game mode).
2. **Into a speaker or mixer with a microphone input** (most "party" or karaoke speakers have one): the PC plays
   into it, the mic plugs into it. No delay at all, and its own knobs. Pick this if the voice through the PC
   feels late, for example through a TV that has no Game mode.
3. **Into the Mac (option D)**: with the Mac route, the Mac mixes the mic (level and reverb); the card then reads
   *Microphone (on the Mac)*.

Plugging a microphone in while karaoke is already on: press *Stop karaoke* and *Start karaoke* once, so the list
sees it. The choice is remembered; if that mic is missing next time, the card says so.

Which microphone:

- **Wired, USB, "dynamic cardioid"** is the right kind for a room with speakers: it hears the singer's mouth and
  little of the speakers, so it rarely howls. A handheld one with a USB-C cable plugs into a Mac or a Windows PC
  without drivers. Examples of the type (not endorsements): Audio-Technica ATR2100x-USB, Samson Q2U.
- **Avoid** Bluetooth microphones (too late, see above) and clip-on "lavalier" kits for vlogging (they hear the
  whole room, so the speakers make them howl).
- **Howling (feedback)**: point the microphone away from the speakers, stand beside or behind them, and raise the
  mic volume slowly from zero. *Noise gate* helps between phrases; lots of reverb or echo makes howling likelier.
- **The meter turns orange**: the mic is too loud at its input and will distort. Lower its level in Windows
  (Settings > System > Sound > Input > the mic > Input volume), then use the card's slider for loudness.

## Test it (do this with the human, in the room)

1. Play a song you know. **Expect:** after the usual wait, the song comes out of the TV or speakers without the
   singer.
2. Watch the lyrics screen. **Expect:** each line lights up a moment before it is sung. If they feel late or
   early, use *Lyrics timing* (it is saved).
3. Scan the QR code with a phone and press next. **Expect:** Spotify skips; the new song starts after the wait.
4. With a microphone: pick it in the *Microphone* card, then say "one, two" while the card's meter moves, and
   raise the level slowly. **Expect:** your voice from the speakers at once. A delay you can hear means Bluetooth
   or casting is in the sound path, or the TV is not in Game mode. A howl means the mic points at the speakers or
   is too loud. Then try Reverb and Echo on a song.
