"""The helper: serves the pages and runs karaoke sessions. Idles with nothing loaded until a page starts one.

  pythonw -m karaoke.agent        always-on: started at logon by a Startup-folder shortcut; no window
  pythonw -m karaoke              the desktop icon (desk.py): runs this only while its window is open

HTTP on port 8765 (the home network only):
  GET  /            phone control page            GET  /tv              lyrics page for the TV
  GET  /state       JSON: settings, heard track + position, status
  GET  /lyrics?serial=N   lyrics of that track (loads in the background; poll until "loading" is false)
  POST /start  /stop       start or stop a session (Spotify routing, model, audio); /start {"output": "mac"}
                           plays on the living-room Mac instead of the PC's speakers
  GET  /audio[?ahead=s]   the Mac player's stream (see netsink.py); ahead = the buffer it wants, more when away
  POST /transport {"action": "next" | "prev" | "playpause" | "shuffle"}   Spotify's own buttons;
                  {"action": "seek", "value": 83.5} jumps to that many seconds into the song
  GET  /art?serial=N   that track's cover image (404 if none)
  POST /set    {"vocal": 30, "key": -2, ...}   live settings; model / delay / speakers restart the engine
  POST /bye    the desktop window is closing (sent as a beacon); see DESK_GONE

The main thread only runs queued commands: PortAudio's WASAPI streams must open and close on one thread.
"""

import atexit
import json
import logging
import os
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import approuting
from .session import ENGINE_LOCK_PORT, Session, restore_spotify

PORT = 8765
PLAYER_GONE = 90            # seconds without the Mac player before a Mac session stops by itself
# The desktop window polls /state with ?desk=1. A hidden or minimised window is throttled by the browser to
# about one poll a minute, so only this long a silence means it is gone. Closing it sends /bye: then 3 s, so a
# page reload (which also sends /bye) does not stop the music.
DESK_GONE = 90
DESK_BYE = 3
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "agent.log")
PAGES = {"/": ("control.html", "text/html"), "/tv": ("tv.html", "text/html"),
         "/theme.css": ("theme.css", "text/css"), "/theme.js": ("theme.js", "text/javascript"), "/i18n.js": ("i18n.js", "text/javascript"), "/qr.js": ("qr.js", "text/javascript")}


def phone_url(conn):
    """The control page's address as other phones on the home network reach it (for the QR on the TV page):
    the address this request came in on, or the Wi-Fi address when the page was opened on the PC itself."""
    ip = conn.getsockname()[0]
    if ip.startswith("127.") or ip == "::1":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))      # no packet is sent; this just picks the outgoing interface
            ip = s.getsockname()[0]
        except OSError:
            pass
        finally:
            s.close()
    return f"http://{ip}:{PORT}/"


def local_addresses():
    """This PC's IPv4 addresses, Tailscale's 100.x one included: the Mac saves them at home so it can still find
    the PC from somewhere else."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos if not i[4][0].startswith("127.")})


def engine_free():
    """True when no engine runs anywhere on this PC (the engine holds ENGINE_LOCK_PORT while it runs)."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", ENGINE_LOCK_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


class Agent:
    def __init__(self, port=PORT, desk=False):
        self.cmds = queue.Queue()
        self.session = Session(call_main=self.post)
        self.desk = desk                # started by the desktop icon: exit once its window is gone
        self.desk_owned = False         # the running session was started by the desktop window
        # Until the window's first poll, give it time to open; a window that never appears must not leave a
        # ghost process behind.
        self.desk_deadline = time.monotonic() + DESK_GONE if desk else None
        self.on_ready = lambda: None
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path in PAGES:
                    name, ctype = PAGES[u.path]
                    with open(os.path.join(WEB, name), "rb") as f:
                        return self._send(200, f.read(), ctype + "; charset=utf-8")
                if u.path == "/audio":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.end_headers()
                    self.close_connection = True
                    try:
                        ahead = float((parse_qs(u.query).get("ahead") or ["0"])[0])
                    except ValueError:
                        ahead = 0.0
                    return agent.session.sink.serve(self.connection, self.rfile, self.wfile, ahead)
                if u.path == "/state":
                    if parse_qs(u.query).get("desk"):
                        agent.desk_deadline = time.monotonic() + DESK_GONE
                    return self._send(200, dict(agent.session.state(), phone_url=phone_url(self.connection),
                                                  player_connects=agent.session.sink.connects,
                                                  player_stalls=agent.session.sink.stalls,
                                                  player_underruns=agent.session.sink.player_underruns,
                                                  player_buffer=agent.session.sink.ahead,
                                                  addresses=local_addresses(), hostname=socket.gethostname()))
                if u.path == "/art":
                    art = agent.session.watcher.art(int((parse_qs(u.query).get("serial") or ["0"])[0]))
                    return self._send(200, art[1], art[0]) if art else self._send(404, {"error": "no cover"})
                if u.path == "/lyrics":
                    serial = int((parse_qs(u.query).get("serial") or ["0"])[0])
                    return self._send(200, agent.session.lyrics_for(serial) or {"serial": serial, "missing": True})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                u = urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._send(400, {"error": "bad json"})
                s = agent.session
                if u.path == "/start":
                    body = body if isinstance(body, dict) else {}
                    if body.get("desk") and not s.active:
                        agent.desk_owned = True
                    agent.post(lambda: s.start(body.get("output", "pc")))
                elif u.path == "/stop":
                    agent.post(s.stop)
                elif u.path == "/bye":
                    if agent.desk_deadline is not None:
                        agent.desk_deadline = min(agent.desk_deadline, time.monotonic() + DESK_BYE)
                elif u.path == "/transport" and isinstance(body, dict):
                    return self._send(200, {"ok": s.watcher.command(body.get("action"), body.get("value"))})
                elif u.path == "/set" and isinstance(body, dict):
                    agent.post(lambda: s.set(**body))
                else:
                    return self._send(404, {"error": "not found"})
                self._send(200, {"ok": True})

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True

    def post(self, fn, *args):
        self.cmds.put((fn, args))

    def run(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.on_ready()
        last_check = last_rescue = 0.0
        player_seen = time.monotonic()
        while True:
            try:
                fn, args = self.cmds.get(timeout=0.25)
                fn(*args)
            except queue.Empty:
                pass
            except Exception:
                logging.exception("command failed")
            if self.session.active and time.monotonic() - last_check > 2:
                last_check = time.monotonic()
                self.session.route_check()
            if (not self.session.active and time.monotonic() - last_rescue > 10
                    and time.monotonic() - self.session.stopped_at > 5):     # a stop restores by itself
                # Crash recovery: Spotify's routing is persisted by Windows, so after a crash or a power cut it
                # would stay on the cable, silent. With no engine running anywhere, nothing should be there.
                last_rescue = time.monotonic()
                try:
                    if approuting.spotify_on_cable() and engine_free():
                        logging.warning("Spotify was left on the cable with no engine running: restoring it")
                        restore_spotify()
                except Exception:
                    logging.exception("crash recovery check failed")
            if self.desk_deadline is not None and time.monotonic() > self.desk_deadline:
                self.desk_deadline = None
                # This process exists only for the window: when it goes, so does every session it hosts.
                if (self.desk_owned or self.desk) and self.session.active:
                    self.session.stop()
                self.desk_owned = False
                if self.desk:
                    return
            # Safety net: a Mac session whose player vanished (laptop shut, Wi-Fi gone) and stayed away would
            # leave Spotify silent on the PC. Reconnects after a hiccup or a network switch come well inside this.
            s = self.session
            if not (s.active and s.output == "mac") or s.sink.connected:
                player_seen = time.monotonic()
            elif time.monotonic() - player_seen > PLAYER_GONE:
                logging.warning("Mac player gone for %d s: stopping the session", PLAYER_GONE)
                s.stop()
                player_seen = time.monotonic()


def main():
    logging.basicConfig(filename=LOG, level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    atexit.register(restore_spotify)
    try:
        Agent().run()
    except Exception:
        logging.exception("helper failed to start")     # pythonw has no console: the log is all there is
        raise


if __name__ == "__main__":
    main()
