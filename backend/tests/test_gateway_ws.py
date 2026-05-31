"""
Gateway WebSocket contract (hermetic) — proves the flood fix: the gateway's /ws ACCEPTS the
upgrade (so the UI never hammers a non-existent socket) and replays the process backlog on
connect, including a spawned process's streamed log lines. A fake subprocess stands in for PX4.

We spawn before connecting and pump the loop with cheap GETs so the reader drains the fake
stdout into the ring buffer; then a fresh connection's backlog is read deterministically
(proc frame + the one buffered log line) — avoiding the TestClient deadlock where a blocking
receive_json on the test thread can't be interleaved with a concurrent spawn.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi.testclient import TestClient

import backend.supervisor as sup
from backend.app import app


class _FakeStdout:
    def __init__(self, lines): self._lines = list(lines)
    async def readline(self):
        if self._lines:
            return (self._lines.pop(0) + "\n").encode()
        await asyncio.sleep(3600)                    # keep the pipe open like a real launcher
        return b""

class _FakeProc:
    def __init__(self, lines): self.pid = 4242; self.returncode = None; self.stdout = _FakeStdout(lines)
    def send_signal(self, s): self.returncode = -int(s)
    def kill(self): self.returncode = -9
    async def wait(self):                            # short procs (e.g. pkill) return immediately
        return self.returncode if self.returncode is not None else 0


def test_gateway_ws_accepts_and_replays_logs(monkeypatch):
    async def fake_exec(*argv, **kw):
        return _FakeProc(["[t5] Launching show: x.json"])
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(sup.glob, "glob", lambda *a, **k: [])      # teardown must not rm real /tmp socks

    with TestClient(app) as client:
        # 1) The socket ACCEPTS and replays a proc backlog frame — the regression (the gateway
        #    used to have no /ws, so the browser flooded the console with failed-handshake 400s).
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "proc"

        # 2) Spawn a fake process via the gateway; pump the loop so its reader drains stdout.
        r = client.post("/api/bringup", json={"target": "player", "n": 1, "show": "x.json"}).json()
        assert r["ok"] and r["target"] == "player"
        for _ in range(5):
            client.get("/api/procs")                 # each round-trip lets the reader run

        assert "player" in client.get("/api/procs").json()

        # 3) A fresh connection replays the buffered backlog: one proc frame + the streamed log
        #    line. Read exactly the backlog size (2) so we never block on the live q.get().
        with client.websocket_connect("/ws") as ws:
            got = [ws.receive_json(), ws.receive_json()]
        assert any(f["type"] == "proc" for f in got)
        assert any(f.get("type") == "log" and "Launching show" in f.get("line", "") for f in got)

        client.post("/api/teardown")                 # faked pkill (create_subprocess_exec patched)
