"""
Integration tests for the run-script connect phase (`_connect_fleet`).

Drives `run_commander._connect_fleet` / `run_skyforge._connect_fleet` with a stubbed
MAVSDK `System` (records ctor host/port; instant connect + ready health) and a
recorded (not spawned) `asyncio.create_subprocess_exec`, asserting the profile flags
actually steer the wiring: beacon-spawned-iff-`use_gcs_beacon`, N local server spawns
iff `spawn_local_server`, and `System` built from `conn.grpc_host`/`grpc_port`.

`asyncio.sleep` is monkeypatched to a no-op so the staggers don't slow the suite.
"""
import asyncio
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))


# ── Fake mavsdk System (records ctor args; instant connect + ready health) ──────
class _Health:
    is_global_position_ok = True
    is_home_position_ok = True


class _Telem:
    async def health(self):
        yield _Health()

    async def set_rate_position_velocity_ned(self, hz):
        pass


class FakeSystem:
    instances = []

    def __init__(self, mavsdk_server_address=None, port=None):
        self.addr = mavsdk_server_address
        self.port = port
        self.telemetry = _Telem()
        FakeSystem.instances.append(self)

    async def connect(self):
        pass


def _install_mavsdk_stub():
    mav = sys.modules.get("mavsdk") or types.ModuleType("mavsdk")
    sys.modules["mavsdk"] = mav
    mav.System = FakeSystem                      # overwrite whatever a sibling test stubbed
    if not getattr(mav, "__file__", None):       # run_*.py derives the bin path from this
        mav.__file__ = os.path.join(tempfile.gettempdir(), "fake_mavsdk", "__init__.py")
    off = sys.modules.get("mavsdk.offboard") or types.ModuleType("mavsdk.offboard")
    sys.modules["mavsdk.offboard"] = off
    if not hasattr(off, "OffboardError"):
        class OffboardError(Exception): ...
        class PositionNedYaw:
            def __init__(self, *a, **k): ...
        off.OffboardError = OffboardError
        off.PositionNedYaw = PositionNedYaw


_install_mavsdk_stub()

from show import audio_beat as _ab
_ab._AUDIO_OK = False   # keep the BeatDetector input-device thread from starting

import run_commander
import run_skyforge
from show.connection import load_profile


def _fleet_file(data: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


def _run_connect(module, fleet_dict: dict, n_for_load: int):
    """Build a profile from a temp fleet file, then run module._connect_fleet with a
    stubbed System + recorded subprocess + no-op sleep. Returns (active, cmd_strings, rt)."""
    path = _fleet_file(fleet_dict)
    try:
        prof = load_profile(n_for_load, path)
    finally:
        os.unlink(path)

    rt = types.SimpleNamespace(ready_target=0)
    FakeSystem.instances = []
    recorded = []
    orig_exec, orig_sleep = asyncio.create_subprocess_exec, asyncio.sleep

    async def _rec_exec(*a, **k):
        recorded.append(a)
        return types.SimpleNamespace()

    async def _fast_sleep(_s):
        pass

    asyncio.create_subprocess_exec = _rec_exec
    asyncio.sleep = _fast_sleep
    try:
        active = asyncio.run(module._connect_fleet(prof.n, prof, rt))
    finally:
        asyncio.create_subprocess_exec = orig_exec
        asyncio.sleep = orig_sleep
    cmds = [" ".join(str(x) for x in a) for a in recorded]
    return active, cmds, rt


_TWO_SITL = {"drones": [{"mavlink_url": "udpin://0.0.0.0:15000"},
                        {"mavlink_url": "udpin://0.0.0.0:15001"}]}


# ── commander ─────────────────────────────────────────────────────────────────

def test_commander_beacon_and_spawns_by_default():
    active, cmds, rt = _run_connect(run_commander, _TWO_SITL, 2)
    assert len(active) == 2 and rt.ready_target == 2
    assert any("14550" in c for c in cmds)                          # beacon spawned (default)
    assert sum(("15000" in c or "15001" in c) for c in cmds) == 2   # 2 local server spawns


def test_commander_beacon_skipped_when_disabled():
    active, cmds, rt = _run_connect(
        run_commander, {"use_gcs_beacon": False, **_TWO_SITL}, 2)
    assert len(active) == 2
    assert not any("14550" in c for c in cmds)                      # beacon NOT spawned


def test_commander_no_local_spawn_when_disabled():
    active, cmds, rt = _run_connect(run_commander, {
        "use_gcs_beacon": False, "spawn_local_server": False,
        "drones": [{"mavlink_url": "udp://10.0.0.5:14550", "grpc_host": "10.0.0.5"}]}, 1)
    assert len(active) == 1                                         # System still connects
    assert cmds == []                                              # nothing spawned locally


def test_commander_system_uses_conn_host_and_port():
    _run_connect(run_commander, {
        "spawn_local_server": False, "use_gcs_beacon": False,
        "drones": [{"mavlink_url": "udp://10.0.0.7:14550",
                    "grpc_host": "10.0.0.7", "grpc_port": 50090}]}, 1)
    assert any(s.addr == "10.0.0.7" and s.port == 50090 for s in FakeSystem.instances)


# ── skyforge player ───────────────────────────────────────────────────────────

def test_skyforge_beacon_and_spawns_by_default():
    active, cmds, rt = _run_connect(run_skyforge, _TWO_SITL, 2)
    assert len(active) == 2 and rt.ready_target == 2
    assert any("14550" in c for c in cmds)
    assert sum(("15000" in c or "15001" in c) for c in cmds) == 2


def test_skyforge_no_beacon_no_spawn_when_disabled():
    active, cmds, rt = _run_connect(run_skyforge, {
        "use_gcs_beacon": False, "spawn_local_server": False,
        "drones": [{"mavlink_url": "udpin://0.0.0.0:15000"}]}, 1)
    assert len(active) == 1 and cmds == []
