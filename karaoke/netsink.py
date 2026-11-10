"""Output stage for the living-room Mac: the mixed backing goes over the home network instead of the speakers.

One player at a time connects with `GET /audio[?ahead=seconds]` and keeps the connection open:
  PC -> Mac   frames: int32 n, then n stereo int16 frames at 48 kHz;  n == -1 means "drop what you have queued"
  Mac -> PC   text lines "queued_frames device_latency_s", ~20 per second

The Mac's playback clock paces the mixer: `size` (frames still to be heard) is the Mac's last report plus what
was sent since, minus the time gone by. So drift between the two machines never accumulates, and the vocal
slider and key stay as responsive as on the PC (only `ahead` seconds are ever in flight).
"""

import logging
import socket
import struct
import threading
import time

import numpy as np

log = logging.getLogger(__name__)
FLUSH = struct.pack("<i", -1)
NO_PLAYER = 10 ** 9         # reported as `size` with no player connected: the mixer waits instead of mixing


MAX_AHEAD = 5.0             # the most the buffer grows to ride out stalls (the vocal slider then lags this much)


class NetSink:
    sr = 48000
    ahead = 0.6             # seconds in flight; Wi-Fi stalls of a few hundred ms must not empty the Mac's queue

    def __init__(self):
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.connects = 0   # players connected so far; more than one per session means the stream broke
        self.stalls = 0     # times the player went quiet for a while (Wi-Fi), see serve()
        self.player_underruns = 0
        self.last_send = 0.0
        self.conn = None    # (socket, write file) of the current player
        self.latency = 0.0
        self._reset_estimate()

    def _reset_estimate(self):
        self.queued_ack, self.ack_time, self.sent_since_ack = 0, time.monotonic(), 0

    @property
    def connected(self):
        return self.conn is not None

    def serve(self, sock, rfile, wfile, ahead=None):
        """Run on the HTTP handler's thread for one player; returns when it disconnects or is replaced.
        `ahead` is the buffer the player asks for: the home Wi-Fi default, or more when it is away (Tailscale).
        Either way it grows by itself after each stall that would have emptied the player's queue."""
        sock.settimeout(10)
        with self.lock:
            old, self.conn = self.conn, (sock, wfile)
            self.connects += 1
            self.ahead = min(MAX_AHEAD, max(0.3, ahead)) if ahead else NetSink.ahead
            self._reset_estimate()
        if old:
            self._drop(old)
        me = self.conn
        threading.Thread(target=self._keepalive, args=(me,), daemon=True).start()
        try:
            for line in rfile:
                parts = line.split()
                if len(parts) >= 2:
                    now = time.monotonic()
                    gap = now - self.ack_time
                    with self.lock:
                        self.queued_ack, self.latency = int(parts[0]), float(parts[1])
                        self.ack_time, self.sent_since_ack = now, 0
                    if len(parts) >= 3:
                        self.player_underruns = int(parts[2])
                    # Reports come ~20 times a second; a long silence means the Wi-Fi stalled.
                    if gap > 0.25:
                        self.stalls += 1
                        if gap + 0.3 > self.ahead:
                            self.ahead = min(MAX_AHEAD, gap + 0.3)
                        log.warning("player stalled %.2f s; it now holds %.2f s, underruns %d, buffer %.1f s", gap,
                                    self.queued_ack / self.sr, self.player_underruns, self.ahead)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                if self.conn is me:
                    self.conn = None

    def _keepalive(self, conn):
        """An empty frame every second while no audio flows (buffering after a skip takes as long as the delay,
        ~6 s at "~5.5 s"), so the player never mistakes the quiet for a dead PC and reconnects mid-song."""
        while self.conn is conn:
            if time.monotonic() - self.last_send > 1.0:
                self._send(struct.pack("<i", 0))
            time.sleep(0.5)

    def _drop(self, conn):
        try:
            conn[0].shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _send(self, data):
        conn = self.conn
        if conn is None:
            return
        try:
            # The mixer pushes and the separation worker clears (on a skip) from different threads: one writer at
            # a time, or two frames interleave and the Mac reads garbage lengths.
            with self.write_lock:
                conn[1].write(data)
                conn[1].flush()
                self.last_send = time.monotonic()
        except OSError:
            with self.lock:
                if self.conn is conn:
                    self.conn = None
            self._drop(conn)

    # The pipeline uses these exactly like its speaker buffer.
    def push(self, x):
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
        with self.lock:
            self.sent_since_ack += len(pcm)
        self._send(struct.pack("<i", len(pcm)) + pcm.tobytes())

    def clear(self):
        with self.lock:
            self._reset_estimate()
        self._send(FLUSH)

    @property
    def size(self):
        if self.conn is None:
            return NO_PLAYER
        with self.lock:
            played = (time.monotonic() - self.ack_time) * self.sr
            return max(0, int(self.queued_ack + self.sent_since_ack - played))
