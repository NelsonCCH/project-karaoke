"""The desktop icon: `pythonw -m karaoke`.

Opens the control page in its own app window (Edge or Chrome, else the default browser) and starts karaoke.
Closing that window stops karaoke and puts Spotify straight back on the speakers.

If the always-on helper is already running, the window simply talks to it. Otherwise this process becomes the
helper for as long as the window is open, then exits, so nothing is left running in the background.
"""

import ctypes
import json
import logging
import os
import subprocess
import sys
import traceback
import urllib.request
import webbrowser

from .agent import LOG, PORT

URL = f"http://localhost:{PORT}/?desk=1"


def message(text, error=True):
    """A plain Windows message box: pythonw has no console, so this is the only way to say what went wrong."""
    ctypes.windll.user32.MessageBoxW(None, text, "Project Karaoke", 0x10 if error else 0x40)


def helper_running():
    """True if Project Karaoke already answers on the port, False if the port is free, None if something else
    holds it."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=2) as r:
            return "settings" in json.loads(r.read())
    except (OSError, ValueError):
        pass
    import socket
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", PORT))
        return False
    except OSError:
        return None
    finally:
        s.close()


def open_window():
    """An app window (no tabs, no address bar) so it feels like a program, not a web page."""
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        for exe in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            path = os.path.join(base or "", exe)
            if base and os.path.exists(path):
                subprocess.Popen([path, f"--app={URL}", "--window-size=520,900"])
                return
    webbrowser.open(URL)


def main():
    logging.basicConfig(filename=LOG, level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    try:
        running = helper_running()
        if running is None:
            return message(f"Port {PORT} is used by another program, so Project Karaoke cannot start.\n\n"
                           "Close that program, or restart the PC, and try again.")
        if running:
            return open_window()
        import atexit
        from .agent import Agent
        from .session import restore_spotify
        atexit.register(restore_spotify)
        agent = Agent(desk=True)
        agent.on_ready = open_window
        agent.run()
    except Exception:
        logging.exception("desktop start failed")
        message("Project Karaoke could not start:\n\n" + traceback.format_exc(limit=3) +
                f"\nThe full error is in {LOG}.\n"
                "Running  .venv\\Scripts\\python tools\\doctor.py  checks this PC for what is missing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
