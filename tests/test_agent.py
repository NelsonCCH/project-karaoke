"""The helper's HTTP API on a free port, without starting the engine (no model, no Spotify routing)."""

import json
import threading
import urllib.request

import pytest

from karaoke.agent import Agent


@pytest.fixture
def agent():
    a = Agent(port=0)
    a.session.save = lambda: None           # never touch the real settings.json
    threading.Thread(target=a.server.serve_forever, daemon=True).start()
    yield a
    a.server.shutdown()
    a.session.watcher.stop()


def call(a, path, body=None):
    url = f"http://127.0.0.1:{a.server.server_address[1]}{path}"
    data = None if body is None else json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST" if data else "GET")) as r:
        return r.headers.get("Content-Type"), r.read()


def run_queued(a):
    while not a.cmds.empty():
        fn, args = a.cmds.get_nowait()
        fn(*args)


def test_state_pages_and_live_set(agent):
    ctype, body = call(agent, "/state")
    s = json.loads(body)
    assert s["active"] is False and s["position"] is None
    assert s["settings"]["delay"] in s["options"]["delay"]
    for path in ("/", "/tv", "/theme.js", "/theme.css", "/i18n.js"):
        ctype, body = call(agent, path)
        assert len(body) > 500 and ctype.startswith("text/")
    call(agent, "/set", {"vocal": 140, "key": 3, "lyrics_offset": -300, "bogus": 1})
    run_queued(agent)
    st = agent.session.settings
    assert (st["vocal"], st["key"], st["lyrics_offset"]) == (100, 3, -300)
    assert "bogus" not in st
    assert "Less bass" in s["options"]["eq"]
    call(agent, "/set", {"eq": "Less bass"})
    call(agent, "/set", {"eq": "No such preset"})
    run_queued(agent)
    assert st["eq"] == "Less bass"
    assert json.loads(call(agent, "/lyrics?serial=999")[1])["missing"] is True
