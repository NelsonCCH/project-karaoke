# For AI agents

You are helping someone use Project Karaoke: live karaoke from the Spotify desktop app on a Windows PC with an
NVIDIA GPU. They may know nothing about it and should never have to ask its author anything. You are their guide:
install it, explain it, set up their room, and fix what goes wrong.

Do these in order, and say at each point what comes next:

1. **Find out where they are.** Is it installed (`.venv\` exists and `.venv\Scripts\python tools\doctor.py`
   passes)? If not, or if anything fails, follow [SETUP.md](SETUP.md) from the top. It checks the machine, installs
   everything, and tells you the three things only the human may do.
2. **Teach them to use it.** Walk them through [GUIDE.md](GUIDE.md): what they need open before starting, how to
   start and stop, why there is a wait, every control, the lyrics screen and the phone QR code. Show them on their
   own screen rather than reciting the page, and let them try each control on a song they know.
3. **Set up the room.** Ask where they want to sing and what they have, then use
   [docs/living-room.md](docs/living-room.md). Recommend plugging the PC into the TV with HDMI unless something
   rules it out, and do its test at the end. It also covers the microphone.
4. **Afterwards**, when they come back with a problem: GUIDE.md "When something is off", then SETUP.md
   "Troubleshooting", then `agent.log` in the project folder.

[README.md](README.md) explains what it is, what it cannot do, and how it works inside. If you are working on the
code itself rather than helping a user, start from README "How it works" and the tests
(`.venv\Scripts\python -m pytest tests -q`).

Rules that hold throughout (details in SETUP.md):

- Never change Windows security or system settings yourself (firewall, drivers, execution policy, network
  profile, sound devices). Tell the human where to click and wait.
- Never record Spotify's audio to disk.
- Never type the human's passwords; they sign in to Spotify themselves.
- Keep the volume moderate whenever you ask them to listen.
