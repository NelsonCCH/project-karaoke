Project Karaoke - the Mac player
================================

The optional "play it in another room" setup: the PC separates, this Mac plays the karaoke, mixes the mic and
shows the lyrics. Design notes are in docs/mac-route.md. The PC needs the always-on helper (see SETUP.md).

First time:
  1. Put this folder anywhere on the Mac (clone the repository, or copy the mac folder).
  2. Open Terminal (Cmd+Space, type Terminal, Return), type "bash " (with a space), drag run.sh from Finder
     into the Terminal window, and press Return.
  3. A menu appears. Type a number and press Return. The first time, it installs a small tool (uv) by itself,
     and puts a "Karaoke" icon on the Desktop.

Every day: double-click "Karaoke" on the Desktop. To stop: press Stop karaoke on the phone page, press Return
in the window, or close it. The PC then puts Spotify back to normal and the window closes by itself (the first
time, macOS may ask to let Terminal control Terminal: allow it). If you move this folder, run run.sh once more so the icon follows it.

  1) Sing: plays the karaoke streamed from the PC, opens the lyrics in the browser.
     The first run finds the PC by scanning the Wi-Fi and remembers it (~/karaoke-bench/player.json).
     Use headphones while trying the mic (the mic starts muted; turn it up on the phone page).
     Phones control it through this Mac: scan the code on the lyrics screen (any phone on the same Wi-Fi).
     If macOS asks whether Python may accept incoming network connections, allow it.
     If it asks to let Terminal use the microphone, allow it.
  2) Can this Mac run the models? 10-20 min, downloads ~2 GB of models the first time. Keep it plugged in.
  3-5) How late is sound through the TV? Needs the Mac connected to the TV.
     3 = Sound output set to the MacBook speakers
     4 = Sound output set to the TV (menu bar > Sound)
     5 = Same as 4, with the TV in Game Mode
  Test results go to results.txt in this folder.

Singing away from home (a friend's place):
  - Once: install Tailscale (tailscale.com) on the PC and on this Mac, signed in to the same account, then sing
    once at home so this Mac remembers the PC's Tailscale address. Leave Tailscale switched off on this Mac:
    the Karaoke icon switches it on only when the PC is not on the Wi-Fi, and off again at the end.
  - The PC stays on at home with Tailscale on. The controls react ~2 s late.
  - Phones need nothing installed: they scan the code and talk to this Mac.

If it says "Operation not permitted": the folder is somewhere macOS guards (iCloud Drive, Desktop, Documents)
and Terminal has no access. Move the folder to your home folder, or allow Terminal under System Settings >
Privacy & Security > Full Disk Access, then quit Terminal (Cmd+Q) and try again.
