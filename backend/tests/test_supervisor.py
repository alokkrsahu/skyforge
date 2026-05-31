"""
Supervisor core (hermetic): env composition, the SITL readiness parser, launch argv, and
ordered teardown with a fake subprocess. Live spawning needs PX4 (verified manually).
"""
import asyncio
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import backend.supervisor as sup
from backend.supervisor import compose_env, parse_sitl_ready, launch_argv, Supervisor, LAUNCHERS


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


class _FakeProc:
    _n = 0
    def __init__(self): _FakeProc._n += 1; self.pid = 4000 + _FakeProc._n; self.returncode = None; self.signals = []
    def send_signal(self, s): self.signals.append(s); self.returncode = -int(s)
    async def wait(self): return self.returncode if self.returncode is not None else 0
    def kill(self): self.returncode = -9


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
    procs = []
    async def fake_exec(*argv, **kw):
        p = _FakeProc(); procs.append(p); return p
    monkeypatch.setattr(sup.asyncio, "create_subprocess_exec", fake_exec)

    async def body():
        s = Supervisor(root="/repo")
        await s.spawn("sitl", n=4)
        first = s.procs["sitl"]
        await s.spawn("sitl", n=8)                 # respawn same target
        assert first.returncode is not None        # old proc was stopped, not orphaned
        assert s.procs["sitl"] is not first        # tracking the new one
    asyncio.run(body())
