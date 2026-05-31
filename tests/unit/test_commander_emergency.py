"""
Tests for the fleet-emergency commands (hold / land / rtl / estop) in the
interactive commander. Driven via asyncio.run() (no pytest-asyncio); mavsdk is a
runtime-only dep, stubbed before importing the adapter. These exercise the
FleetCommander state machine and the REPL alias routing — no hardware.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

# ── Stub mavsdk before importing the adapter ──────────────────────────────────
_m = types.ModuleType("mavsdk"); _m.System = object
_off = types.ModuleType("mavsdk.offboard")
class OffboardError(Exception): ...
class PositionNedYaw:
    def __init__(self, *a, **k): ...
_off.OffboardError = OffboardError
_off.PositionNedYaw = PositionNedYaw
sys.modules.setdefault("mavsdk", _m)
sys.modules.setdefault("mavsdk.offboard", _off)

from commander.dynamic_adapter import DynamicRuntime
from commander.commander import FleetCommander
from commander import cli


def _cmd(n=4):
    rt = DynamicRuntime(n, [(2.0 * (i // 2), 2.0 * (i % 2)) for i in range(n)])
    rt.airborne = True
    rt.alt_m = 6.0
    return FleetCommander(rt), rt


# ── FleetCommander state machine ──────────────────────────────────────────────

def test_estop_lands_immediately():
    cmd, rt = _cmd()
    asyncio.run(cmd.abort())
    assert rt.abort_flag is True and rt.airborne is False   # immediate, no stagger


def test_land_staggered_vs_immediate():
    cmd, rt = _cmd()
    asyncio.run(cmd.land(stagger=True))
    assert rt.abort_flag is False and rt.airborne is False   # staggered descent
    cmd, rt = _cmd()
    asyncio.run(cmd.land(stagger=False))
    assert rt.abort_flag is True                             # immediate


def test_hold_cancels_transition():
    cmd, rt = _cmd()
    rt.start_transition({i: (9.0, 9.0, -6.0) for i in range(rt.n_drones)}, 6.0)
    assert rt.transition is not None
    asyncio.run(cmd.hover())
    assert rt.transition is None                             # frozen in place


def test_rtl_returns_home_then_lands():
    cmd, rt = _cmd()
    async def body():
        msg = await cmd.rtl(0.05)
        assert "RTL" in msg
        # transition targets each drone's home XY at cruise altitude
        for i in range(rt.n_drones):
            tgt = rt.transition.end_pos[i]
            assert tgt[0] == rt.home_ned[i][0] and tgt[1] == rt.home_ned[i][1]
            assert abs(tgt[2] - (-rt.alt_m)) < 1e-9
        await asyncio.sleep(0.12)            # let the scheduled land fire
        assert rt.airborne is False
    asyncio.run(body())


def test_rtl_noop_when_not_airborne():
    cmd, rt = _cmd()
    rt.airborne = False
    assert "not airborne" in asyncio.run(cmd.rtl()).lower()
    assert rt.transition is None


# ── REPL alias routing (estop→abort, hold→hover, rtl, land now) ───────────────

class _Recorder:
    def __init__(self):
        self.calls = []
        self.runtime = types.SimpleNamespace(airborne=False)
    def _mk(name):
        async def f(self, *a, **k): self.calls.append((name, a, k)); return name
        return f
    abort = _mk("abort"); hover = _mk("hover"); land = _mk("land")
    async def rtl(self, t=8.0): self.calls.append(("rtl", t)); return "rtl"


def _verb(line):
    rec = _Recorder()
    asyncio.run(cli._dispatch(line, rec))
    return rec.calls


def test_dispatch_aliases():
    assert _verb("estop")[0][0] == "abort"
    assert _verb("hold")[0][0] == "hover"
    assert _verb("rtl 5")[0] == ("rtl", 5.0)
    assert _verb("land now")[0][2] == {"stagger": False}     # immediate
    assert _verb("land")[0][2] == {"stagger": True}          # staggered
