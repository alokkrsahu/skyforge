"""
Tests for shared-T0 synchronization: the epoch→monotonic mapping and the
scheduled-start primitive (a future start_at holds the fleet at start_pos until T0).
mavsdk stubbed (runtime-only dep).
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

from show.time_sync import monotonic_for_epoch, resolve_t0_epoch
from commander.dynamic_adapter import DynamicRuntime


# ── epoch → monotonic mapping ─────────────────────────────────────────────────

def test_monotonic_for_epoch_offsets_by_wall_delta():
    # epoch 10 s in the future of now_wall → 10 s ahead of now_mono
    assert monotonic_for_epoch(1010.0, now_wall=1000.0, now_mono=500.0) == 510.0
    # an epoch in the past maps to a past monotonic value
    assert monotonic_for_epoch(995.0, now_wall=1000.0, now_mono=500.0) == 495.0


def test_resolve_t0_epoch_env():
    old = os.environ.get("SKYFORGE_T0_EPOCH")
    try:
        os.environ.pop("SKYFORGE_T0_EPOCH", None)
        assert resolve_t0_epoch() is None                 # unset → None
        os.environ["SKYFORGE_T0_EPOCH"] = "   "
        assert resolve_t0_epoch() is None                 # blank → None
        import time
        os.environ["SKYFORGE_T0_EPOCH"] = str(time.time() + 30.0)
        dl = resolve_t0_epoch()
        assert dl is not None and dl > time.monotonic()   # future deadline
    finally:
        if old is None: os.environ.pop("SKYFORGE_T0_EPOCH", None)
        else: os.environ["SKYFORGE_T0_EPOCH"] = old


# ── scheduled (synchronized) start ────────────────────────────────────────────

def test_scheduled_start_holds_until_t0_then_moves():
    rt = DynamicRuntime(2, [(0.0, 0.0), (0.0, 2.0)])
    mono = 1000.0
    end = {0: (5.0, 0.0, -8.0), 1: (5.0, 2.0, -8.0)}
    rt.start_transition(end, duration_s=4.0, start_at=mono + 10.0)   # T0 = +10 s
    # before T0: every drone holds at its start_pos (the pre-move hold position)
    for i in (0, 1):
        assert rt.target_ned(i, mono) == rt.transition.start_pos[i]
    # at/after T0+duration: at the end position
    assert rt.target_ned(0, mono + 14.0) == end[0]


def test_default_start_is_immediate():
    rt = DynamicRuntime(1, [(0.0, 0.0)])
    end = {0: (3.0, 0.0, -6.0)}
    rt.start_transition(end, duration_s=2.0)              # no start_at → start now
    # start_time ~ now, so well past it the drone reaches the end
    import time
    assert rt.target_ned(0, time.monotonic() + 5.0) == end[0]
