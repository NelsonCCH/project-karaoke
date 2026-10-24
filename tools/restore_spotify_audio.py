"""Put Spotify back on Windows' default output (normal listening). Project Karaoke does this itself when it
stops; this is the manual fallback if it was killed or crashed and Spotify stayed silent.

Windows keeps the routing per app, and it can only be changed while Spotify runs, so this opens Spotify first.

usage: pythonw tools/restore_spotify_audio.py        (the "Spotify Normal Audio" desktop shortcut)
"""

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from karaoke import approuting


def main():
    if not approuting.pids():
        os.startfile("spotify:")
        for _ in range(30):
            time.sleep(0.5)
            if approuting.pids():
                break
        time.sleep(2)               # its audio process starts a moment after the window
    touched = approuting.route_spotify(False)
    on = approuting.spotify_on_cable()
    if not touched:
        text = "Spotify is not running, so nothing was changed. Open Spotify and double-click this again."
    elif on:
        text = "Spotify still plays into the cable. Quit Spotify completely (tray icon > Quit) and try again."
    else:
        text = "Spotify plays on your normal speakers again."
    ctypes.windll.user32.MessageBoxW(None, text, "Spotify Normal Audio", 0x40)


if __name__ == "__main__":
    main()
