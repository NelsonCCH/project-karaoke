"""The Mac stream end to end over a real socket: frames arrive intact, acks pace the sender, flush drops."""

import socket
import struct
import threading
import time

import numpy as np

from karaoke.agent import Agent


def read_exact(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        assert c, "stream closed"
        b += c
    return b


def read_frame_len(s):
    """Next frame length, skipping the keep-alive empty frames."""
    while True:
        n = struct.unpack("<i", read_exact(s, 4))[0]
        if n != 0:
            return n


def test_stream_frames_acks_and_flush():
    a = Agent(port=0)
    threading.Thread(target=a.server.serve_forever, daemon=True).start()
    sink = a.session.sink
    try:
        assert sink.size > 10 ** 8                      # no player: the mixer would wait
        s = socket.create_connection(("127.0.0.1", a.server.server_address[1]))
        s.sendall(b"GET /audio HTTP/1.1\r\nHost: x\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += read_exact(s, 1)
        assert b"200" in head.split(b"\r\n")[0]
        for _ in range(50):
            if sink.connected:
                break
            time.sleep(0.02)

        x = (np.arange(960 * 2, dtype=np.float32).reshape(960, 2) / 4000) - 0.2
        sink.push(x)
        n = read_frame_len(s)
        got = np.frombuffer(read_exact(s, n * 4), "<i2").reshape(n, 2) / 32767
        assert n == 960 and np.abs(got - x).max() < 1e-4
        assert 900 < sink.size <= 960                   # sent, not yet acknowledged

        s.sendall(b"4800 0.025\n")                      # the Mac says 0.1 s queued, 25 ms device latency
        time.sleep(0.1)
        assert abs(sink.latency - 0.025) < 1e-9
        assert 0 <= sink.size < 4800                    # and it keeps draining with the clock

        # A stall longer than the buffer (Wi-Fi / internet hiccup): the buffer grows to ride out the next one.
        assert abs(sink.ahead - 0.6) < 1e-9
        time.sleep(1.0)
        s.sendall(b"0 0.025 3\n")
        time.sleep(0.1)
        assert 1.25 < sink.ahead < 1.5 and sink.stalls == 1 and sink.player_underruns == 3

        sink.clear()
        assert read_frame_len(s) == -1

        # Nothing to send (buffering after a skip): an empty frame still arrives every second or so.
        s.settimeout(2.5)
        assert struct.unpack("<i", read_exact(s, 4))[0] == 0
        s.close()
        for _ in range(50):
            if not sink.connected:
                break
            time.sleep(0.02)
        assert not sink.connected

        # A player away from home asks for a bigger buffer up front.
        s = socket.create_connection(("127.0.0.1", a.server.server_address[1]))
        s.sendall(b"GET /audio?ahead=2 HTTP/1.1\r\nHost: x\r\n\r\n")
        for _ in range(50):
            if sink.connected:
                break
            time.sleep(0.02)
        assert sink.ahead == 2.0
        s.close()
    finally:
        a.server.shutdown()
        a.session.watcher.stop()
