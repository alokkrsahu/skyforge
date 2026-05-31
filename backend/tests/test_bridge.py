"""
Bridge-plane tests (no MAVSDK, no PX4): drive the REAL FleetCommander + DynamicRuntime
through the FastAPI control app via TestClient. Asserts the verb→endpoint routing, the
guard tri-state matrix, the snapshot shape, that abort is always callable, and that the
peek/target side-effect rule holds.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

# Stub mavsdk (runtime-only dep) before importing the commander.
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
from backend.control import build_app, classify


def _client(n=4, airborne=False):
    rt = DynamicRuntime(n, [(2.0 * (i // 2), 2.0 * (i % 2)) for i in range(n)])
    rt.airborne = airborne
    rt.ready_target = n
    app = build_app(FleetCommander(rt), rt, asyncio.Event(), None)
    return TestClient(app), rt


# ── classify tri-state ─────────────────────────────────────────────────────────

def test_classify_tristate():
    assert classify("Transitioning to 'circle' over 6.0s", "formation") == {
        "ok": True, "guard": False, "status": "Transitioning to 'circle' over 6.0s", "verb": "formation"}
    assert classify("Error: Unknown formation 'hexagon'", "formation")["ok"] is False
    assert classify("Drones not airborne yet (0 in offboard...)", "formation")["guard"] is True
    assert classify("Unknown colour 'mauve'. Known: red, ...", "color")["guard"] is True


# ── guard matrix (grounded fleet) ───────────────────────────────────────────────

def test_guarded_verbs_block_when_grounded():
    c, _ = _client(airborne=False)
    for path, body in [("/api/cmd/formation", {"spec": "circle"}),
                       ("/api/cmd/move", {"dN": 1.0, "dE": 0.0}),
                       ("/api/cmd/rtl", {})]:
        r = c.post(path, json=body).json()
        assert r["ok"] is True and r["guard"] is True and "not airborne" in r["status"]


def test_unguarded_verbs_run_when_grounded():
    c, rt = _client(airborne=False)
    assert c.post("/api/cmd/takeoff", json={"altitude_m": 6.0}).json()["guard"] is False
    assert c.post("/api/cmd/altitude", json={"alt_m": 10.0}).json() == {
        "ok": True, "guard": False, "status": "Altitude → 10.0 m over 5.0 s", "verb": "altitude"}
    assert c.post("/api/cmd/color", json={"name": "blue"}).json()["ok"] is True
    assert c.post("/api/cmd/color", json={"name": "mauve"}).json()["guard"] is True   # unknown colour
    assert c.post("/api/cmd/hover", json={}).json()["ok"] is True


def test_abort_always_callable_estop():
    c, _ = _client(airborne=True)
    r = c.post("/api/cmd/abort").json()
    assert r["ok"] is True and r["guard"] is False and "ABORT" in r["status"]


def test_session_kill_sets_abort_event():
    rt = DynamicRuntime(2, [(0, 0), (0, 2)])
    ev = asyncio.Event()
    c = TestClient(build_app(FleetCommander(rt), rt, ev, None))
    assert c.post("/api/session/kill").json()["ok"] is True
    assert ev.is_set()


# ── airborne happy path + snapshot ──────────────────────────────────────────────

def test_formation_runs_when_airborne():
    c, rt = _client(n=6, airborne=True)
    r = c.post("/api/cmd/formation", json={"spec": "circle", "transition_s": 4.0}).json()
    assert r["ok"] and not r["guard"] and "Transitioning" in r["status"]
    snap = c.get("/api/snapshot").json()
    assert snap["airborne"] is True and len(snap["drones"]) == 6
    d0 = snap["drones"][0]
    assert set(d0) == {"id", "pos", "vel", "target", "stale"}
    assert d0["target"] is not None and len(d0["target"]) == 3   # peek_target ghost


def test_status_text_endpoint():
    c, _ = _client()
    assert "Fleet:" in c.get("/api/status").json()["text"]


def test_zero_transition_does_not_crash_snapshot():
    # transition_s:0 must not make peek_target/snapshot raise ZeroDivisionError.
    c, _ = _client(n=4, airborne=True)
    r = c.post("/api/cmd/move", json={"dN": 5.0, "dE": 0.0, "transition_s": 0.0}).json()
    assert r["ok"] is True
    snap = c.get("/api/snapshot").json()            # the path that used to crash
    assert len(snap["drones"]) == 4 and snap["drones"][0]["target"] is not None


# ── single-writer command lock ───────────────────────────────────────────────

def test_command_lock_enforced_except_abort():
    c, _ = _client(airborne=True)
    tok = c.post("/api/command/acquire").json()["token"]
    # without the token, a mutating verb is locked out (409)...
    r = c.post("/api/cmd/hover")
    assert r.status_code == 409 and r.json()["guard"] is True
    # ...with the token it runs...
    assert c.post("/api/cmd/hover", headers={"x-command-token": tok}).json()["ok"] is True
    # ...and abort/E-STOP is NEVER lockable.
    assert c.post("/api/cmd/abort").json()["status"].startswith("ABORT")
    c.post("/api/command/release")
    assert c.post("/api/cmd/hover").json()["ok"] is True   # open again after release
