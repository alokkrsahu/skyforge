"""
WebSocket telemetry schema/cadence, the peek-vs-target side-effect rule, and the
forbidden-pattern contract (the backend must never touch a MAVSDK telemetry stream).
"""
import asyncio
import os
import pathlib
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

_m = types.ModuleType("mavsdk"); _m.System = object
_off = types.ModuleType("mavsdk.offboard")
class OffboardError(Exception): ...
class PositionNedYaw:
    def __init__(self, *a, **k): ...
_off.OffboardError = OffboardError; _off.PositionNedYaw = PositionNedYaw
sys.modules.setdefault("mavsdk", _m); sys.modules.setdefault("mavsdk.offboard", _off)

from fastapi.testclient import TestClient
from commander.dynamic_adapter import DynamicRuntime
from commander.commander import FleetCommander
from backend.control import build_app


def test_ws_streams_telemetry_frames():
    rt = DynamicRuntime(3, [(0, 0), (0, 2), (0, 4)])
    rt.airborne = True
    c = TestClient(build_app(FleetCommander(rt), rt, asyncio.Event(), None))
    with c.websocket_connect("/ws") as ws:
        f1 = ws.receive_json()
        f2 = ws.receive_json()                       # second frame → proves cadence/stream
        for f in (f1, f2):
            assert f["type"] == "telemetry"
            assert f["airborne"] is True and len(f["drones"]) == 3
            assert "t" in f and "transition" in f


def test_peek_target_does_not_clear_transition_but_target_ned_does():
    rt = DynamicRuntime(2, [(0, 0), (0, 2)])
    # a transition that's already 'finished' (start in the past, short duration → alpha>=1)
    import time
    rt.start_transition({0: (5.0, 0.0, -8.0), 1: (5.0, 2.0, -8.0)}, duration_s=0.001,
                        start_at=time.monotonic() - 10.0)
    assert rt.transition is not None
    rt.peek_target(0, time.monotonic())              # read-only
    assert rt.transition is not None                 # NOT cleared
    rt.target_ned(0, time.monotonic())               # mutating
    assert rt.transition is None                      # cleared


def test_backend_never_touches_mavsdk_stream():
    """Contract: the web layer must read the 10 Hz cache only — never subscribe to (or
    wait_for) position_velocity_ned(), which permanently breaks the MAVSDK generator."""
    backend_dir = pathlib.Path(__file__).resolve().parents[1]
    for py in backend_dir.rglob("*.py"):
        if "tests" in py.parts:                          # the tests themselves name the forbidden API
            continue
        src = py.read_text()
        assert "position_velocity_ned" not in src, f"{py} subscribes to the telemetry stream"
        if "wait_for" in src:
            assert "telemetry" not in src.lower(), f"{py} wraps telemetry in wait_for"
