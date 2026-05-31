"""
Supervisor core (hermetic): env composition, the SITL readiness parser, launch argv, output
capture + log streaming, the per-process state machine, and ordered teardown with reader
cancellation — all with a fake subprocess that models a long-running process (stdout stays
open until the process is stopped, like t1's blocking `wait`). Live spawning needs PX4 and is
verified manually.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import backend.supervisor as sup
from backend.supervisor import compose_env, parse_sitl_ready, launch_argv, Supervisor, LAUNCHERS


@pytest.fixture(autouse=True)
def _no_real_fs(monkeypatch):
    """teardown() globs + removes /tmp/px4-sock-*,px4_lock-* — never touch a dev machine's real
    sockets from a unit test."""
    monkeypatch.setattr(sup.glob, "glob", lambda *a, **k: [])


# ── A fake subprocess: yields canned stdout lines, then keeps the pipe open (blocking on
#    readline) until the process is signalled — mirroring a real long-running launcher. ──
class _FakeStdout:
    def __init__(self, proc, lines): self._proc = proc; self._lines = list(lines)
    async def readline(self):
        if self._lines:
            return (self._lines.pop(0) + "\n").encode()
        await self._proc._dead.wait()                 # pipe stays open until the proc dies
        return b""

class _FakeProc:
    _n = 0
    def __init__(self, lines=()):
        _FakeProc._n += 1
        self.pid = 4000 + _FakeProc._n
        self.returncode = None
        self.signals = []
        self._dead = asyncio.Event()
        self.stdout = _FakeStdout(self, lines)
    def send_signal(self, s): self.signals.append(s); self.returncode = -int(s); self._dead.set()
    async def wait(self):                             # returns immediately (short procs like pkill);
        return self.returncode if self.returncode is not None else 0   # long-running is modeled by
    def kill(self): self.returncode = -9; self._dead.set()             # readline blocking on _dead


class _Hub:
    """Minimal FastAPI-app stand-in: just app.state.subscribers (a set of frame queues)."""
    def __init__(self):
        self.state = type("S", (), {})()
        self.state.subscribers = set()
    def sink(self) -> asyncio.Queue:
        q = asyncio.Queue(); self.state.subscribers.add(q); return q


def _drain(q: asyncio.Queue, typ=None) -> list:
    out = []
    while not q.empty():
        f = q.get_nowait()
        if typ is None or f.get("type") == typ: out.append(f)
    return out


def _fake_exec_returning(lines_by_call):
    """A create_subprocess_exec replacement; `lines_by_call` is a list consumed per spawn."""
    calls = list(lines_by_call)
    async def fake_exec(*argv, **kw):
        return _FakeProc(calls.pop(0) if calls else ())
    return fake_exec


# ── pure helpers (unchanged) ──────────────────────────────────────────────────
def test_compose_env():
    e = compose_env({"gcs": "qgc", "led": "stub", "autoabort": True, "web": True, "web_port": 8799,
                     "blackbox": "/tmp/f.jsonl"})
    assert e["SKYFORGE_GCS"] == "qgc" and e["SKYFORGE_LED_BACKEND"] == "stub"
    assert e["SKYFORGE_AUTOABORT"] == "1" and e["SKYFORGE_WEB"] == "1"
    assert e["SKYFORGE_WEB_PORT"] == "8799" and e["SKYFORGE_BLACKBOX"] == "/tmp/f.jsonl"
    assert compose_env({}) == {}                                  # only set what's given
    assert compose_env({"t0_epoch": 0})["SKYFORGE_T0_EPOCH"] == "0"  # legitimate 0 forwarded
    assert "SKYFORGE_GCS" not in compose_env({"gcs": ""})           # empty string skipped


def test_parse_sitl_ready():
    text = ("INFO startup\nStartup script returned successfully\n...\n"
            "Startup script returned successfully\n")
    assert parse_sitl_ready(text) == 2
    assert parse_sitl_ready("") == 0


def test_launch_argv():
    root = "/repo"
    assert launch_argv(root, "sitl", n=16, arena="walls") == ["bash", "/repo/runtime/t1_sitl.sh", "16", "walls"]
    assert launch_argv(root, "commander", n=8)[-1] == "8"
    assert launch_argv(root, "agents", n=4, show="s.json") == ["bash", "/repo/runtime/t8_agents.sh", "s.json", "4"]
    assert launch_argv(root, "qgc") == ["bash", "/repo/runtime/t7_qgc.sh"]


# ── output capture + state machine ─────────────────────────────────────────────
def test_reader_streams_log_frames(monkeypatch):
    hub = _Hub(); q = hub.sink()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", _fake_exec_returning([["hello", "world"]]))

    async def body():
        s = Supervisor(root="/repo", app=hub)
        await s.spawn("player", n=1, show="x.json")
        await asyncio.sleep(0.05)                         # let the reader drain the pipe
        assert [f["line"] for f in _drain(q, "log")] == ["hello", "world"]
        assert [f["line"] for f in s.rings["player"]] == ["hello", "world"]
        await s.teardown()
    asyncio.run(body())


def test_sitl_readiness_transitions(monkeypatch):
    hub = _Hub(); q = hub.sink()
    lines = ["Startup script returned successfully"] * 4
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", _fake_exec_returning([lines]))

    async def body():
        s = Supervisor(root="/repo", app=hub)
        await s.spawn("sitl", n=4)
        await asyncio.sleep(0.05)
        assert [(f["n"], f["of"]) for f in _drain(q, "ready")] == [(1, 4), (2, 4), (3, 4), (4, 4)]
        assert s.status()["sitl"]["state"] == "running"
        assert s._ready_events["sitl"].is_set()
        await s.teardown()
    asyncio.run(body())


def test_sitl_failure_state(monkeypatch):
    hub = _Hub()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec",
                        _fake_exec_returning([["Startup script returned with return value: 1"]]))

    async def body():
        s = Supervisor(root="/repo", app=hub)
        await s.spawn("sitl", n=4)
        await asyncio.sleep(0.05)
        assert s.status()["sitl"]["state"] == "failed"
        assert await s._await_ready("sitl", timeout=0.1) is False    # failure → not ready
        await s.teardown()
    asyncio.run(body())


def test_commander_readiness_marker(monkeypatch):
    hub = _Hub()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec",
                        _fake_exec_returning([["[web] SkyForge operator UI bridge on http://127.0.0.1:8799"]]))

    async def body():
        s = Supervisor(root="/repo", app=hub)
        await s.spawn("commander", n=4, opts={"web": True})
        await asyncio.sleep(0.05)
        assert s.status()["commander"]["state"] == "ready"
        assert s._ready_events["commander"].is_set()
        await s.teardown()
    asyncio.run(body())


# ── spawn / teardown ───────────────────────────────────────────────────────────
def test_spawn_and_ordered_teardown(monkeypatch):
    spawned = []
    async def fake_exec(*argv, **kw):
        spawned.append(argv); return _FakeProc()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", fake_exec)

    async def body():
        s = Supervisor(root="/repo")
        await s.spawn("sitl", n=4)
        await s.spawn("commander", n=4, opts={"web": True})
        assert set(s.status()) == {"sitl", "commander"}
        killed = await s.teardown()
        # commander (a flight runtime) is torn down before sitl
        assert killed.index("commander") < killed.index("sitl")
        assert s.procs == {}
    asyncio.run(body())
    assert any("t6_commander.sh" in a for argv in spawned for a in argv)


def test_respawn_stops_old_proc(monkeypatch):
    async def fake_exec(*argv, **kw):
        return _FakeProc()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", fake_exec)

    async def body():
        s = Supervisor(root="/repo")
        await s.spawn("sitl", n=4)
        first = s.procs["sitl"]
        await s.spawn("sitl", n=8)                 # respawn same target
        assert first.returncode is not None        # old proc was stopped, not orphaned
        assert s.procs["sitl"] is not first        # tracking the new one
        await s.teardown()
    asyncio.run(body())


def test_orchestrated_launch_sequence(monkeypatch):
    hub = _Hub(); q = hub.sink()
    # 1st spawn (sitl) emits 4 readiness lines; 2nd (commander) emits the bridge marker.
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", _fake_exec_returning([
        ["Startup script returned successfully"] * 4,
        ["[web] SkyForge operator UI bridge on http://127.0.0.1:8799"],
    ]))

    async def body():
        s = Supervisor(root="/repo", app=hub)
        async def fake_bridge(port, timeout=90.0): return True      # don't really HTTP-probe
        s._await_bridge = fake_bridge
        await s.orchestrate(n=4)
        frames = _drain(q)                                          # drain once, then filter
        assert [f["phase"] for f in frames if f["type"] == "lifecycle"] == \
            ["sitl_starting", "sitl_ready", "commander_starting", "bridge_up"]
        bringups = [f for f in frames if f["type"] == "bringup"]
        assert bringups and bringups[-1]["port"] == 8799
        assert not s._orchestrating                                 # cleared in finally
        await s.teardown()
    asyncio.run(body())


def test_orchestrated_launch_times_out_on_sitl(monkeypatch):
    hub = _Hub(); q = hub.sink()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", _fake_exec_returning([[]]))  # sitl never readies

    async def body():
        s = Supervisor(root="/repo", app=hub)
        await s.orchestrate(n=2)                                    # _await_ready times out fast below
        phases = [f["phase"] for f in _drain(q, "lifecycle")]
        assert phases[0] == "sitl_starting" and phases[-1] == "timeout"
        assert "commander_starting" not in phases                  # never advanced past SITL
        await s.teardown()
    # shrink the SITL readiness timeout so the test is fast
    import backend.supervisor as _sup
    real = _sup.Supervisor._await_ready
    async def quick(self, target, timeout=240.0): return await real(self, target, timeout=0.1)
    monkeypatch.setattr(_sup.Supervisor, "_await_ready", quick)
    asyncio.run(body())


def test_terminal_mode_spawns_osascript(monkeypatch):
    calls = []
    async def fake_exec(*argv, **kw):
        calls.append(argv); return _FakeProc()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", fake_exec)

    async def body():
        s = Supervisor(root="/repo")
        await s.spawn("sitl", n=2, mode="terminal")
        assert any(a and a[0] == "osascript" for a in calls)       # opened a Terminal window
        assert "sitl" in s.terms and "sitl" not in s.procs         # tracked as terminal (no PID)
        assert s.status()["sitl"]["running"] and s.status()["sitl"]["state"] == "starting"
        await s.teardown()
        assert "sitl" not in s.terms                               # pkill'd + untracked
    asyncio.run(body())


def test_teardown_aborts_inflight_launch(monkeypatch):
    hub = _Hub(); q = hub.sink()
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", _fake_exec_returning([[]]))  # sitl never readies

    async def body():
        s = Supervisor(root="/repo", app=hub)
        s._orchestrating = True
        s._launch_task = asyncio.create_task(s.orchestrate(n=2))   # blocks awaiting SITL ready
        await asyncio.sleep(0.05)
        await s.teardown()                                         # must abort the launch
        assert s._launch_task.done()
        assert not s._orchestrating
        assert "aborted" in [f["phase"] for f in _drain(q, "lifecycle")]
    asyncio.run(body())


def test_teardown_cancels_readers(monkeypatch):
    async def fake_exec(*argv, **kw):
        return _FakeProc()                          # never EOFs on its own → reader stays alive
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", fake_exec)

    async def body():
        s = Supervisor(root="/repo")
        await s.spawn("commander", n=4, opts={"web": True})
        reader = s.readers["commander"]
        assert not reader.done()
        await s.teardown()
        assert reader.done()                        # reader cancelled/joined on teardown
        assert s.procs == {}
    asyncio.run(body())
