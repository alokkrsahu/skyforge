"""
Tests for mid-show resilience: detecting a drone whose telemetry has gone stale
and acting per fail_mode (continue = drop it, the show goes on; abort = fleet land).
mavsdk is stubbed (runtime-only dep). No hardware.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

_m = types.ModuleType("mavsdk"); _m.System = object
_off = types.ModuleType("mavsdk.offboard")
class OffboardError(Exception): ...
class PositionNedYaw:
    def __init__(self, *a, **k): ...
_off.OffboardError = OffboardError
_off.PositionNedYaw = PositionNedYaw
sys.modules.setdefault("mavsdk", _m)
sys.modules.setdefault("mavsdk.offboard", _off)

from commander.dynamic_adapter import DynamicRuntime, apply_health_policy, DROPOUT_TIMEOUT_S


def _rt(n=4):
    rt = DynamicRuntime(n, [(0.0, 2.0 * i) for i in range(n)])
    rt.airborne = True
    now = 100.0
    for i in range(n):                          # all fresh at t=now
        rt.position_timestamps[i] = now
        rt.current_positions[i] = (0.0, 2.0 * i, -5.0)
        rt.current_velocities[i] = (0.0, 0.0, 0.0)
    return rt, now


def test_no_loss_when_all_fresh():
    rt, now = _rt()
    assert apply_health_policy(rt, now, timeout=2.0) == []


def test_continue_mode_drops_stale_drone():
    rt, now = _rt()
    rt.fail_mode = "continue"
    rt.position_timestamps[2] = now - 5.0       # drone 2 went stale
    lost = apply_health_policy(rt, now, timeout=2.0)
    assert lost == [2]
    assert 2 not in rt.current_positions         # dropped from live caches
    assert 2 not in rt.position_timestamps
    assert rt.airborne is True and rt.abort_flag is False   # show continues
    assert 0 in rt.current_positions             # survivors untouched


def test_abort_mode_lands_fleet_on_loss():
    rt, now = _rt()
    rt.fail_mode = "abort"
    rt.position_timestamps[1] = now - 5.0
    lost = apply_health_policy(rt, now, timeout=2.0)
    assert lost == [1]
    assert rt.abort_flag is True and rt.airborne is False    # fleet emergency land


def test_noop_when_not_airborne():
    rt, now = _rt()
    rt.airborne = False
    rt.position_timestamps[0] = now - 99.0
    assert apply_health_policy(rt, now) == []                # nothing acted


def test_default_fail_mode_is_continue():
    rt, _ = _rt()
    assert rt.fail_mode == "continue"                        # env default
