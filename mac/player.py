# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "sounddevice"]
# ///
"""Project Karaoke - living-room player for the Mac.

The PC (in the bedroom) separates the vocals; this script plays the backing it streams, mixes in the mic with
some echo, and opens the lyrics page. Started from run.sh. Press Return (or close Terminal) to stop: the PC
then puts Spotify back to normal.

Mic level and echo are set on the phone page; the mic starts at 0 every time so there is no feedback squeal.
The phone page and the lyrics page are relayed by this Mac (port 8765), so phones only need the same Wi-Fi as
the Mac, at home or away (the Mac reaches the PC over Tailscale then).
"""

import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.expanduser("~/karaoke-bench/player.json")
PORT = 8765
SR = 48000
BLOCK = 256                 # frames per audio callback, ~5 ms: the mic's own share of the voice latency
AWAY_BUFFER = 2.0           # seconds of audio buffered over the internet (home Wi-Fi: the PC's 0.6 s default)


# ---- finding the PC ----------------------------------------------------------------------------------------

def get_json(host, path, timeout=1.5):
    with urllib.request.urlopen(f"http://{host}:{PORT}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def post(host, path, body=None):
    req = urllib.request.Request(f"http://{host}:{PORT}{path}", data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def is_karaoke(host, timeout=1.5):
    try:
        return "settings" in get_json(host, "/state", timeout)
    except Exception:
        return False


def my_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))      # no packet is sent; this just picks the Wi-Fi interface's address
        return s.getsockname()[0]
    finally:
        s.close()


def scan_subnet():
    """Every address on this Mac's /24 that answers on the karaoke port."""
    prefix = my_lan_ip().rsplit(".", 1)[0]

    def probe(i):
        host = f"{prefix}.{i}"
        try:
            socket.create_connection((host, PORT), timeout=0.4).close()
            return host if is_karaoke(host) else None
        except OSError:
            return None

    with ThreadPoolExecutor(64) as ex:
        return [h for h in ex.map(probe, range(1, 255)) if h]


def load_conf():
    try:
        with open(CONF) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def remember(host, conf):
    """Save where the PC was found, its name, and its Tailscale address (100.x) for when this Mac is away.
    Nothing about the PC is built in: the first run finds it with a scan of the Wi-Fi."""
    try:
        st = get_json(host, "/state")
    except Exception:
        st = {}
    remote = [a for a in st.get("addresses", []) if is_tailscale(a)]
    home = conf.get("pc") if is_tailscale(host) else host       # "pc" stays the home address
    conf = {"pc": home, "name": st.get("hostname") or conf.get("name"), "remote": remote or conf.get("remote", [])}
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF, "w") as f:
        json.dump(conf, f)


def is_tailscale(host):
    """Tailscale hands out 100.64.0.0/10 addresses; reaching the PC through one means this Mac is away."""
    try:
        a, b = (int(x) for x in socket.gethostbyname(host).split(".")[:2])
    except (OSError, ValueError):
        return False
    return a == 100 and 64 <= b < 128


# Tailscale is only switched on when the PC is not on this network (away from home), and off again at the end,
# so it is never left running on this Mac. The app's own command-line tool does the switching.
TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
TAILSCALE_SWITCHED = False          # set once this run switches Tailscale on


def tailscale(*args, timeout=20):
    try:
        return subprocess.run([TAILSCALE, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def tailscale_up():
    """Switch Tailscale on if it is off; main() switches it off again at the end (TAILSCALE_SWITCHED)."""
    if not os.path.exists(TAILSCALE):
        return
    r = tailscale("status", timeout=5)
    if r is not None and r.returncode == 0:
        return                    # already on: someone else's choice, leave it on afterwards
    print("Away from home: switching Tailscale on...")
    subprocess.run(["open", "-g", "-a", "Tailscale"])      # the app must run for its tool to work
    global TAILSCALE_SWITCHED
    TAILSCALE_SWITCHED = True           # from here on, main() switches it off again whatever happens
    for _ in range(10):
        r = tailscale("up")
        if r is not None and r.returncode == 0:
            return
        time.sleep(1.5)
    print("Could not switch Tailscale on:", (r.stderr or r.stdout).strip() if r else "no Tailscale app")


def tailscale_down():
    tailscale("down")
    subprocess.run(["osascript", "-e", 'quit app "Tailscale"'], capture_output=True)
    print("Tailscale switched off.")


def find_pc():
    """Home first: the saved address, the PC's name, a scan of this network. Only then Tailscale: switched on
    if needed, then the saved 100.x address and the PC's Tailscale name."""
    conf = load_conf()
    name = conf.get("name")             # the PC's Windows name: NAME.local at home, Tailscale's MagicDNS name away
    for host in dict.fromkeys(h for h in [conf.get("pc"), name and f"{name}.local"] if h):
        if is_karaoke(host):
            remember(host, conf)
            return host
    print("Looking for the PC on this network...")
    found = scan_subnet()
    if found:
        remember(found[0], conf)
        return found[0]
    tailscale_up()
    away = list(dict.fromkeys(conf.get("remote", []) + ([name.lower()] if name else [])))
    deadline = time.monotonic() + 30    # Tailscale can take a while to reach the PC right after switching on
    while time.monotonic() < deadline:
        for host in away:
            if is_karaoke(host, 5):
                remember(host, conf)
                return host
        time.sleep(1)
    sys.exit("Can't find the PC. At home: is it on, and did Windows Firewall allow Python? "
             "Away from home: is the PC on at home with Tailscale running?")


# ---- control-page relay ------------------------------------------------------------------------------------
# Phones talk to this Mac, and the Mac passes everything on to the PC. So any phone on the Wi-Fi where the Mac
# is (home, or a friend's) can scan the QR and control the karaoke, with no Tailscale on the phone.

def start_relay(host, port=PORT):
    """Serve the phone page and the lyrics page on this Mac. Returns the phone address, or None if the port is
    taken (then phones and the browser use the PC directly, which only works at home)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    phone = f"http://{my_lan_ip()}:{port}/"

    class Relay(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.forward(None)

        def do_POST(self):
            self.forward(self.rfile.read(int(self.headers.get("Content-Length") or 0)))

        def forward(self, body):
            if self.path.startswith("/audio"):          # the stream is this player's own business
                return self.reply(404, b"{}", "application/json")
            req = urllib.request.Request(f"http://{host}:{PORT}{self.path}", data=body,
                                         method="POST" if body is not None else "GET",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    code, data, ctype = r.status, r.read(), r.headers.get("Content-Type", "text/plain")
            except urllib.error.HTTPError as e:
                code, data, ctype = e.code, e.read(), e.headers.get("Content-Type", "text/plain")
            except Exception:
                return self.reply(502, b'{"error": "PC unreachable"}', "application/json")
            if self.path.split("?")[0] == "/state":
                s = json.loads(data)
                s["phone_url"] = phone                  # the QR on the TV points phones here, not at the PC
                data = json.dumps(s, ensure_ascii=False).encode()
            self.reply(code, data, ctype)

        def reply(self, code, data, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Relay)
    except OSError:
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return phone


# ---- echo --------------------------------------------------------------------------------------------------

class Echo:
    """Small Schroeder/Freeverb-style room: 4 combs + 2 allpasses. Every delay is longer than one audio block,
    so each filter runs block-wise in numpy with no per-sample Python loop."""

    def __init__(self):
        s = SR / 44100
        self.combs = [np.zeros(int(d * s), np.float32) for d in (1116, 1188, 1277, 1356)]
        self.aps = [(np.zeros(int(d * s), np.float32), np.zeros(int(d * s), np.float32)) for d in (556, 441)]
        self.fb, self.g = 0.84, 0.5         # ~1 s tail: a karaoke-room echo, not a cathedral
        self.tail = np.zeros(3, np.float32)

    def process(self, x):
        n = len(x)
        out = np.zeros(n, np.float32)
        for i, buf in enumerate(self.combs):
            y = x + self.fb * buf[:n]               # buf holds the last D outputs; its oldest n are y[t - D]
            self.combs[i] = np.concatenate([buf[n:], y])
            out += y
        out *= 0.25
        for i, (xb, yb) in enumerate(self.aps):
            y = -self.g * out + xb[:n] + self.g * yb[:n]
            self.aps[i] = (np.concatenate([xb[n:], out]), np.concatenate([yb[n:], y]))
            out = y
        # Soften the metallic top end: 4-tap moving average across block boundaries.
        z = np.concatenate([self.tail, out])
        self.tail = z[-3:]
        return (z[3:] + z[2:-1] + z[1:-2] + z[:-3]) * 0.25


# ---- player ------------------------------------------------------------------------------------------------

def default_devices():
    """(default output id, default input id) straight from Core Audio. PortAudio only reads the device list at
    start-up, so this is how the player notices AirPods (or the TV) being picked in the Sound menu."""
    import ctypes
    ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")

    class Address(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

    def get(selector):
        addr = Address(selector, 0x676C6F62, 0)        # 'glob' scope, main element
        dev, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
        ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(dev))
        return dev.value

    return get(0x644F7574), get(0x64496E20)            # 'dOut', 'dIn '


def pick_mic():
    """The default input, unless it is AirPods / Bluetooth: opening a Bluetooth mic drops the earbuds into the
    low-quality call mode, so the Mac's own mic is used instead. None = the default."""
    name = sd.query_devices(kind="input")["name"]
    if "AirPods" not in name and "Bluetooth" not in name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and "MacBook" in d["name"]:
            return i
    return None


class Player:
    def __init__(self, host):
        self.host = host
        self.away = is_tailscale(host)     # over the internet: ask the PC for a bigger buffer
        self.q, self.qsize = deque(), 0
        self.mic_q, self.mic_size = deque(), 0         # mic frames waiting for the next output block
        self.mic_peak = 0.0
        self.lock = threading.Lock()
        self.mic_gain = 0.0
        self.echo_gain = 0.3
        self.echo = Echo()
        self.running = True
        self.out_latency = 0.05
        self.underruns = 0
        self.had_data = False
        self.sock = None

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("PC closed the stream")
            buf += chunk
        return buf

    def receive(self):
        """Stream reader: keeps reconnecting while running (Wi-Fi hiccups, PC engine restarts)."""
        while self.running:
            try:
                self.sock = socket.create_connection((self.host, PORT), timeout=5)
                query = f"?ahead={AWAY_BUFFER}" if self.away else ""
                self.sock.sendall(f"GET /audio{query} HTTP/1.1\r\nHost: {self.host}\r\n\r\n".encode())
                head = b""
                while b"\r\n\r\n" not in head:
                    head += self._read_exact(1)
                self.sock.settimeout(15)    # the PC sends an empty frame every second while it has no audio
                threading.Thread(target=self.ack, args=(self.sock,), daemon=True).start()
                while self.running:
                    n = struct.unpack("<i", self._read_exact(4))[0]
                    if n < -1 or n > 10 * SR:
                        raise ConnectionError(f"garbled stream (frame length {n})")     # reconnect = resync
                    if n < 0:
                        with self.lock:
                            self.q.clear()
                            self.qsize = 0
                            self.had_data = False   # a skip empties the queue on purpose: not an underrun
                        continue
                    x = np.frombuffer(self._read_exact(n * 4), "<i2").reshape(n, 2).astype(np.float32) / 32768
                    with self.lock:
                        self.q.append(x)
                        self.qsize += n
            except (OSError, ConnectionError, struct.error):
                time.sleep(1)

    def ack(self, sock):
        while self.running and sock is self.sock:
            try:
                sock.sendall(f"{self.qsize} {self.out_latency:.4f} {self.underruns}\n".encode())
            except OSError:
                return
            time.sleep(0.05)

    def follow_settings(self):
        while self.running:
            try:
                st = get_json(self.host, "/state")["settings"]
                self.mic_gain = st.get("mic", 0) / 100 * 2.0       # up to +6 dB
                self.echo_gain = st.get("echo", 30) / 100 * 0.6
            except Exception:
                pass
            time.sleep(0.3)

    def pop(self, n):
        out = np.zeros((n, 2), np.float32)
        got = 0
        with self.lock:
            while got < n and self.q:
                b = self.q[0]
                take = min(n - got, len(b))
                out[got : got + take] = b[:take]
                if take == len(b):
                    self.q.popleft()
                else:
                    self.q[0] = b[take:]
                got += take
            self.qsize -= got
        if got < n and self.had_data:       # ran dry mid-song (the first starved block after real audio)
            self.underruns += 1
        self.had_data = got == n
        return out

    # The mic and the speakers are separate streams: on a Mac they are separate devices anyway (built-in mic +
    # AirPods / TV), and one combined stream fails across devices. Mic frames meet the output through mic_q.
    def mic_callback(self, indata, frames, t, status):
        x = indata[:, 0].copy()
        self.mic_peak = max(self.mic_peak, float(np.abs(x).max()))
        with self.lock:
            self.mic_q.append(x)
            self.mic_size += len(x)
            while self.mic_size > 4 * BLOCK:        # never let the voice fall behind: drop the oldest
                self.mic_size -= len(self.mic_q.popleft())

    def take_mic(self, n):
        out, got = np.zeros(n, np.float32), 0
        with self.lock:
            while got < n and self.mic_q:
                b = self.mic_q[0]
                k = min(n - got, len(b))
                out[got : got + k] = b[:k]
                if k == len(b):
                    self.mic_q.popleft()
                else:
                    self.mic_q[0] = b[k:]
                got += k
            self.mic_size -= got
        return out

    def callback(self, outdata, frames, t, status):
        y = self.pop(frames)
        mic = self.take_mic(frames)
        if self.mic_gain > 0:
            mic = mic * self.mic_gain
            y += (mic + self.echo.process(mic) * self.echo_gain)[:, None]
        np.clip(y, -1, 1, out=outdata)

    def open_streams(self):
        """Open the current default output and input. Returns (output stream, input stream or None, message)."""
        out = sd.OutputStream(samplerate=SR, blocksize=BLOCK, channels=2, dtype="float32", latency="low",
                              callback=self.callback)
        out.start()
        self.out_latency = out.latency
        name = sd.query_devices(kind="output")["name"]
        try:
            mic_dev = pick_mic()
            mic = sd.InputStream(device=mic_dev, samplerate=SR, blocksize=BLOCK, channels=1, dtype="float32",
                                 latency="low", callback=self.mic_callback)
            mic.start()
            voice_ms = (mic.latency + out.latency + 2 * BLOCK / SR) * 1000
            mic_name = sd.query_devices(mic_dev if mic_dev is not None else sd.default.device[0])["name"]
            msg = f"Playing on: {name}. Mic: {mic_name}, mic-to-speaker latency inside this Mac ~{voice_ms:.0f} ms"
            if "AirPods" in name or "Bluetooth" in name:
                msg += " (+ ~150-250 ms of Bluetooth: fine for listening, too slow to sing through)"
        except Exception as e:
            mic, msg = None, f"Playing on: {name}. No microphone ({e})."
        return out, mic, msg

    def run(self):
        threading.Thread(target=self.receive, daemon=True).start()
        threading.Thread(target=self.follow_settings, daemon=True).start()
        stop = threading.Event()

        def wait_for_return():
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
            stop.set()

        threading.Thread(target=wait_for_return, daemon=True).start()
        devices = default_devices()
        out, mic, msg = self.open_streams()
        self.report(msg)
        print("Press Return to stop.")
        silent_warned, started = False, time.monotonic()
        while not stop.wait(1.0):
            now = default_devices()
            if now != devices:          # AirPods / TV / speakers picked in the Sound menu: follow it
                devices = now
                for s in (out, mic):
                    if s:
                        s.stop()
                        s.close()
                sd._terminate()         # PortAudio's device list is fixed at init; re-read it
                sd._initialize()
                out, mic, msg = self.open_streams()
                self.report(msg)
            if mic and not silent_warned and time.monotonic() - started > 4 and self.mic_peak == 0.0:
                silent_warned = True
                print("The mic sends pure silence: macOS is blocking it. System Settings > Privacy & Security > "
                      "Microphone > switch Terminal on, then quit Terminal (Cmd+Q) and start again.")
        for s in (out, mic):
            if s:
                s.stop()
                s.close()
        self.running = False

    def report(self, msg):
        print(msg)
        with open(os.path.join(HERE, "results.txt"), "a", encoding="utf-8") as f:
            f.write(f"\n=== player | {time.strftime('%Y-%m-%d %H:%M')} | {msg}\n")


def main():
    # Closing the Terminal window sends SIGHUP: turn it into a normal exit so the PC is still told to stop.
    signal.signal(signal.SIGHUP, lambda *a: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    try:
        sing()
    finally:
        if TAILSCALE_SWITCHED:
            tailscale_down()


def sing():
    host = find_pc()
    try:
        # Once, here: every request by name (MYPC.local) tries IPv6 first and waits ~2 s for it, which made
        # the relayed phone page take ~10 s to open.
        host = socket.gethostbyname(host)
    except OSError:
        pass
    where = "over Tailscale (away from home)" if is_tailscale(host) else "on the home network"
    print(f"Found the PC at {host}, {where}. Starting the karaoke engine (about 10 s)...")
    if is_tailscale(host):
        print(f"Away from home: {AWAY_BUFFER:.0f} s of buffering, so the vocal and key controls react a bit later.")
    post(host, "/start", {"output": "mac"})
    phone = start_relay(host)
    if phone:
        subprocess.run(["open", f"http://localhost:{PORT}/tv"])
        print(f"Phone control page: {phone}  (or scan the code on the lyrics screen)")
    else:
        subprocess.run(["open", f"http://{host}:{PORT}/tv"])
        print(f"Phone control page: http://{host}:{PORT}/  (port {PORT} is busy on this Mac, so only at home)")
    try:
        Player(host).run()
    finally:
        try:
            post(host, "/stop")
            print("Stopped. Spotify is back to normal on the PC.")
        except Exception:
            print("Could not reach the PC to stop it; use Stop on the phone page.")


if __name__ == "__main__":
    main()
