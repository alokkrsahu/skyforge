"""
Tests for the crash-hardened arm path in the interactive commander.

Background: mavsdk_server ABORTS (std::bad_optional_access in its lazy Action
plugin) if arm() reaches a momentarily-disconnected system — which kills the
server and surfaces as "Connection reset by peer", taking down the fleet at
takeoff under load. The fix gates every arm on a fresh readiness check
(`_ensure_ready`) and respawns + retries a server that dies mid-arm instead of
abandoning the flight.

Driven via asyncio.run() (no pytest-asyncio). mavsdk is a runtime-only dep, so
it is stubbed in sys.modules before importing the adapter.
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

from commander.dynamic_adapter import _ensure_ready, run_drone_commander, DynamicRuntime


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _Health:
    def __init__(self, g, h):
        self.is_global_position_ok, self.is_home_position_ok = g, h


class _Telem:
    """health() mimics MAVSDK: 'ok' yields a ready packet, 'never' streams
    not-ready forever, 'raise' simulates a dead server (stream errors)."""
    def __init__(self, mode):
        self.mode = mode

    async def health(self):
        if self.mode == "raise":
            raise RuntimeError("server down")
        while True:
            if self.mode == "ok":
                yield _Health(True, True)
                return
            yield _Health(False, False)
            await asyncio.sleep(0.001)


class _Action:
    def __init__(self):
        self.arm_calls = 0

    async def arm(self):
        self.arm_calls += 1
        raise RuntimeError("recvmsg:Connection reset by peer")

    async def takeoff(self):
        pass


class _Drone:
    def __init__(self, health_mode="ok"):
        self.telemetry = _Telem(health_mode)
        self.action = _Action()


# ── _ensure_ready ─────────────────────────────────────────────────────────────

def test_ensure_ready_true_when_healthy():
    assert asyncio.run(_ensure_ready(_Drone("ok"), timeout=1.0)) is True


def test_ensure_ready_false_on_timeout():
    """A system that never reports global+home (link up but EKF not converged, or
    a flapping link) must NOT be declared ready — arming it would crash the server."""
    assert asyncio.run(_ensure_ready(_Drone("never"), timeout=0.05)) is False


def test_ensure_ready_false_when_stream_raises():
    """A dead server (health stream errors) is reported not-ready, not propagated."""
    assert asyncio.run(_ensure_ready(_Drone("raise"), timeout=1.0)) is False


# ── arm retry + respawn ───────────────────────────────────────────────────────

def test_arm_failure_respawns_then_retries():
    """When arm() throws (server aborted), the commander respawns that drone's
    server and retries — it does NOT just skip the cycle leaving the server dead.
    The respawn callback aborts after 2 calls so the coroutine exits deterministically."""
    async def body():
        rt = DynamicRuntime(n_drones=1, home_ned_list=[(0.0, 0.0)])
        rt.flight_cycle = 1            # release the coroutine past the wait-for-takeoff
        abort = asyncio.Event()
        drone = _Drone("ok")           # ready check passes; only arm() fails
        calls = {"n": 0}

        async def respawn(_i):
            calls["n"] += 1
            if calls["n"] >= 2:
                abort.set()            # stop after we've proven retry-with-respawn

        await asyncio.wait_for(
            run_drone_commander(0, drone, rt, abort, respawn), timeout=5.0)

        assert calls["n"] == 2         # respawned after attempt 1 and attempt 2
        assert drone.action.arm_calls >= 2   # arm was retried, not abandoned on first failure
        assert rt.airborne is False    # never reached offboard — cleanly skipped, no crash

    asyncio.run(body())


def test_arm_skips_cycle_without_respawn_callback():
    """Backward-compatible: with no respawn callback (respawn_server=None), a
    failing arm still degrades gracefully (skips the cycle) instead of erroring."""
    async def body():
        rt = DynamicRuntime(n_drones=1, home_ned_list=[(0.0, 0.0)])
        rt.flight_cycle = 1
        abort = asyncio.Event()
        drone = _Drone("ok")

        async def stop_soon():
            await asyncio.sleep(0.2)
            abort.set()

        await asyncio.wait_for(
            asyncio.gather(
                run_drone_commander(0, drone, rt, abort, None),
                stop_soon(),
            ),
            timeout=5.0,
        )
        assert drone.action.arm_calls >= 1   # attempted, then skipped — no exception
        assert rt.airborne is False

    asyncio.run(body())
