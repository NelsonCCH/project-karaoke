import os
import subprocess
import sys

# The app runs windowless (pythonw), so any console program it starts would open a terminal window of its own
# for a moment: `ver` from Python's platform module (run by `import torch` and by audio-separator) and
# `ffmpeg -version` (audio-separator, at every model load). Give every child process no window, from the start.
if os.name == "nt" and os.path.basename(sys.executable).lower() == "pythonw.exe":
    _popen_init = subprocess.Popen.__init__

    def _windowless(self, *a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        _popen_init(self, *a, **kw)

    subprocess.Popen.__init__ = _windowless
