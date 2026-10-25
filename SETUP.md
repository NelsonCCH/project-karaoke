# Setup guide for an AI coding agent

You are setting up Project Karaoke on your user's Windows PC. Work through the steps in order. Each step ends
with a **check**: run it and compare against the expected result before moving on. `README.md` explains what
the app is, what it can and cannot do, and how it works. This file is about getting it running; steps 7 and 8
then hand over to [GUIDE.md](GUIDE.md) (teaching the human to use it) and
[docs/living-room.md](docs/living-room.md) (putting it on the TV). Ignore the `mac/` folder unless the human
picks the Mac option there.

## Rules while you work

- **The human does three things you must not do for them.** They install the VB-Audio cable driver (it needs
  admin rights and a reboot). They answer the Windows Firewall prompt. They sign in to Spotify. Ask them, wait,
  then run the check.
- **Change no Windows security or system settings yourself**: no firewall rules, no execution policy, no
  driver installs, no network profile. Reading settings is fine.
- **Never record Spotify audio to disk.** The app only holds it in memory. Test with what plays live.
- Keep the volume moderate when you ask the human to listen.
- Personal state stays out of git, and `.gitignore` already covers it: `settings.json` (their choices),
  `agent.log` and `models/` (downloaded weights).
- Tell the human what each step is for in one plain sentence. Most people have never heard of a "virtual
  audio cable".

## 0. What the PC needs

Ask the human to confirm, or check yourself:

| Need | Check |
|---|---|
| Windows 11 (10 21H2+ may work) | `[Environment]::OSVersion` in PowerShell |
| NVIDIA GPU, GTX 16xx / RTX 20xx or newer, driver 570+ | `nvidia-smi` lists the GPU and a driver version ≥ 570. No NVIDIA GPU (AMD, Intel, older cards): stop here and tell the human this PC cannot run it. |
| Spotify desktop app, signed in | Installed from spotify.com or the Microsoft Store, and plays music. The web player does not count. |
| Python 3.11 | `py -3.11 --version` prints 3.11.x |
| git | `git --version` |
| ~8 GB free disk | doctor checks it in step 3 |

- If Python 3.11 is missing, install it after the human agrees: `winget install Python.Python.3.11` (or the
  python.org installer, which also installs the `py` launcher). A Microsoft Store Python has no `py` launcher;
  use its full path to `python.exe` in step 1 instead.
- If git is missing: `winget install Git.Git`, after the human agrees.
- Windows 10: proceed; `tools\doctor.py` (step 3) is the judge.
- Put the project in a plain folder such as `C:\Projects\Project Karaoke`, **not inside OneDrive** (it would
  sync gigabytes). On many Windows 11 PCs, Documents, Desktop and Pictures are OneDrive folders without the user
  knowing, so do not clone into `Documents\GitHub`: use `C:\Projects`. The virtual environment and the shortcuts point at this folder: if it is ever moved, redo
  steps 1 and 5.

## 1. Python environment

From the repository root:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.lock.txt --extra-index-url https://download.pytorch.org/whl/cu128
```

This downloads about 3 GB (torch with CUDA), takes ~5 GB on disk, and takes several minutes.

If pip cannot find one pinned version (the lock pins `onnx-weekly`, a nightly build that audio-separator
requires, and old nightlies get deleted): copy the lock to a scratch file without the `onnx-weekly` line, install
from that copy with the same command, then `.venv\Scripts\python -m pip install onnx-weekly` (unpinned, from
PyPI). Tell the human which version you used. Any other pin that fails: the same, one package at a time.

**Check:** `.venv\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`
prints `True` and the GPU's name. `False` means the CPU build of torch was installed: rerun the install with
the `--extra-index-url` above, or update the NVIDIA driver.

## 2. VB-Audio Virtual Cable (the human installs it)

Explain: Spotify will play into this virtual cable instead of the speakers, so the app can take the singer out
before you hear it.

Ask the human to:
1. Download "VB-CABLE Driver" from https://vb-audio.com/Cable/ and unzip it.
2. Right-click `VBCABLE_Setup_x64.exe` and pick *Run as administrator*, then *Install Driver*.
3. Reboot.
4. Afterwards open Settings > System > Sound and set **Output** back to their speakers or headphones. The
   installer often makes the cable the default, and then the keyboard volume keys stop working and nothing is
   heard.

**Check:** step 3 below shows `PASS VB-Audio Virtual Cable` and `PASS Windows default output: <their speakers>`.

## 3. Machine check

```powershell
.venv\Scripts\python tools\doctor.py
```

**Expected:** every line PASS. Each FAIL or WARN line says how to fix it. `tools\doctor.py` changes nothing,
so run it as often as you like.

## 4. GPU speed and the delay setting

```powershell
.venv\Scripts\python tools\doctor.py --speed
```

The first run downloads the default model (0.9 GB, into `models/`). It then times the model at every delay
setting. "Load" is the share of real time the GPU needs. Anything under ~60% is comfortable. A delay whose load
is close to 100% will stutter on this machine. Tell the human which delays their GPU can run. The default is
"~4.5 s", and they can change it in the app. With under 8 GB of VRAM, also time the small model:
`tools\doctor.py --speed anvuew-karaoke` (0.2 GB download), and suggest it if the default is too slow.

You can ignore a log line about "CUDAExecutionProvider not available in ONNXruntime": these models run on
torch, not ONNX.

## 5. First start, then the shortcuts

Start it once from a console, so any error is visible:

```powershell
.venv\Scripts\python -m karaoke
```

**Expected:** a small window opens (Edge or Chrome in app mode, else a browser tab) showing "Loading the
model…" and then "Waiting for Spotify" or "Paused". If step 4 was skipped, it first says "Downloading the
model (0.9 GB, first time only)…" for a few minutes, with no progress bar: watch `models\` grow to tell it from a
hang. The first time, **Windows Firewall asks** whether Python may
use the network: the human picks *Allow*. Close the window: the console command ends by itself within a few
seconds.

Then make the shortcuts. Ask the human whether they also want the **always-on helper**: it starts with Windows
and waits in the background, so a phone can start karaoke without anyone touching the PC. Without it, phones
work while the window is open. Most people do not need it; the Mac option in docs/living-room.md does.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1 -NoHelper  # window only (most people)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1            # plus the helper
```

`-ExecutionPolicy Bypass` applies to this one command only and changes no setting.

**Check:** the Desktop has "Project Karaoke" and "Spotify Normal Audio". With the helper, the Startup folder
(`shell:startup`) also has "Project Karaoke Helper"; start it now without logging out with
`Start-Process .venv\Scripts\pythonw.exe -ArgumentList '-m','karaoke.agent'`, and after ~15 s
`Invoke-RestMethod http://localhost:8765/state` returns JSON with `active: False`. A connection error instead
means the helper did not start: the reason is at the end of `agent.log`.

## 6. Listening test (with the human)

1. Double-click **Project Karaoke**. Play a song in Spotify.
2. **Expect:** after about the delay, the song continues with the singer almost gone (the default keeps 10%).
   The window says "Playing".
3. Press ↑ three times (40%): a clear guide voice. Press → twice: the key goes up two semitones and the tempo
   stays the same. Skip to another song: it plays at 10% and key 0. Go back to the first song: it comes back at
   40% and +2 (both are remembered per song). Press `0` to reset the key and `V` for 10%.
4. Click **Lyrics screen**. **Expect:** the song's lyrics, the current line highlighted about half a second
   before it is sung. Songs without synced lyrics on LRCLIB show plain lyrics or "No lyrics found": a gap in
   the data, not a bug.
5. On a phone on the same Wi-Fi, scan the QR code on the lyrics screen. Press next on the phone. **Expect:**
   Spotify skips, and the new song is heard after about the delay.
6. If they have a USB microphone: plug it in before starting (or press Stop karaoke and Start karaoke), pick it
   in the *Microphone* card, and with headphones on or the volume low, speak while raising the level from 0.
   **Expect:** the meter moves, and the voice is heard with no noticeable delay; Reverb and Echo change it at once.
7. Close the window. **Expect:** within ~3 s Spotify pauses, and plays normally on the speakers when they press
   play. (Only a window that is minimised and never closed takes ~90 s to stop by itself.)

Automated tests (about 1-2 min, they use the GPU): `.venv\Scripts\python -m pytest tests -q`. **Expect:** all
pass.

## 7. Teach the human to use it

Setup is not finished until the human can run it alone. Walk them through [GUIDE.md](GUIDE.md) on their own
screen: what must be open before starting (the Spotify desktop app, playing on this PC), start and stop, why there
is a wait and how the queue avoids it, every control including the per-song key, the lyrics screen and the phone
QR code. Let them do each thing once themselves. Ask if anything is unclear before moving on.

## 8. The living room

Ask whether they want it on a TV, and where. Follow [docs/living-room.md](docs/living-room.md): it recommends
plugging this PC into the TV with HDMI first, lists the alternatives (soundbar, Bluetooth for music only, casting
just the lyrics, a Mac by the TV), explains the microphone, and ends with a test to do with them in the room.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Double-clicking the icon does nothing | Run `.venv\Scripts\python -m karaoke` in a console to see the error. Errors are also in `agent.log`. |
| Spotify silent after a crash | Spotify is still routed into the cable. Double-click "Spotify Normal Audio" (it opens Spotify if needed). The app also fixes this by itself the next time it runs. |
| "Spotify won't move to the cable" | Quit Spotify completely (tray icon > Quit), open it again. |
| Volume keys do nothing | The Windows default output is the cable. Settings > Sound > Output: pick the speakers. |
| "Waiting for Spotify" | The Spotify desktop app is not open on this PC, or plays on another device. Open it and play here. |
| Stutters or gaps | The GPU is too slow for this delay, or another program (a game) uses it. Pick a longer delay or the anvuew model; `doctor.py --speed` shows what fits. |
| "Vocal removal failed … memory" | The GPU ran out of memory. Close GPU-heavy programs, or pick a shorter delay. |
| Phone can't open the page | The phone is on another network (guest Wi-Fi), the firewall prompt was declined, or Windows marks this Wi-Fi as *Public*. The human fixes it: Settings > Network & internet > Wi-Fi > (network) > *Private*, and Windows Security > Firewall > *Allow an app through firewall* > Python, ticked for Private. |
| "Another Project Karaoke … is already running the engine" | Something else runs it (the helper, or a test). Press Stop there, or close the other window. |
| Plays on the wrong output | Pick it under Engine settings > Speakers or headphones. It is saved in `settings.json`. |

## Uninstall

`powershell -NoProfile -ExecutionPolicy Bypass -File tools\uninstall.ps1` (with Spotify open), then delete the
folder. README.md "Uninstall" lists the optional driver and firewall clean-up, which the human does.

## Where things are

- `settings.json`: the user's saved choices (model, delay, speakers, vocal level, lyrics offset, sound preset,
  the microphone and its effects, and `song_keys` / `song_vocals`: the key and vocal of every song they changed). Delete it to reset. It is never committed.
- `lyrics_cache.json`: every lyrics answer LRCLIB gave, so songs played again need no lookup. Safe to delete.
- `models/`: downloaded model weights. `karaoke/models.py` lists the models and the delay settings.
- `agent.log`: warnings and errors (the app has no console).
- `docs/mac-route.md`: notes on the optional "play it in another room" setup. Not needed here.
