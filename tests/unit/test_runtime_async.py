"""
Async runtime tests for the skyforge show player.

No pytest-asyncio required: each test drives an async body via asyncio.run().
mavsdk is a runtime-only dependency (not installed in CI), so it is stubbed in
sys.modules before importing the adapter, and the audio BeatDetector is disabled
so no real input-device thread starts during tests.
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

from show import audio_beat as _ab
_ab._AUDIO_OK = False   # keep the BeatDetector input-device thread from starting

from show.skyforge_adapter import SkyforgeRuntime, telemetry_consumer
from core.show_format.schema import (
    ShowFile, ShowMetadata, DroneSpec, Vec3, NominalTrajectory,
    LedTrack, LedKeyframe, Color, DroneEnvelope, EnvelopeSegment,
)


# ── Fixtures / fakes ──────────────────────────────────────────────────────────

def _make_show(n=2, homes=None):
    homes = homes or [(0.0, 0.0)] * n
    return ShowFile(
        metadata=ShowMetadata(n_drones=n, duration_s=10.0),
        drones=[DroneSpec(i, Vec3(homes[i][0], homes[i][1], 0.0)) for i in range(n)],
        trajectories=[NominalTrajectory(i, []) for i in range(n)],
        led_tracks=[LedTrack(i, [LedKeyframe(0.0, Color())]) for i in range(n)],
        envelopes=[DroneEnvelope(i, [EnvelopeSegment(0.0, 10.0, 0.0)]) for i in range(n)],
        reactive_bindings=[],
    )


class _PV:
    class _P:
        def __init__(s, n, e, d): s.north_m, s.east_m, s.down_m = n, e, d
    class _V:
        def __init__(s, n, e, d): s.north_m_s, s.east_m_s, s.down_m_s = n, e, d
    def __init__(self, n, e, d):
        self.position = _PV._P(n, e, d)
        self.velocity = _PV._V(0.1 * n, 0.1 * e, 0.1 * d)


class _Telemetry:
    """position_velocity_ned() yields a batch per call (last batch repeats); a
    'RAISE' marker simulates a dropped MAVSDK stream."""
    def __init__(self, batches):
        self._batches, self._call = batches, 0

    async def position_velocity_ned(self):
        batch = self._batches[min(self._call, len(self._batches) - 1)]
        self._call += 1
        for item in batch:
            if item == "RAISE":
                raise RuntimeError("telemetry stream dropped")
            yield item
            await asyncio.sleep(0)


class _FakeDrone:
    def __init__(self, batches):
        self.telemetry = _Telemetry(batches)


async def _consume_briefly(drone, rt, drone_id, settle):
    abort = asyncio.Event()
    task = asyncio.create_task(telemetry_consumer(drone, drone_id, rt, abort))
    await asyncio.sleep(settle)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_telemetry_consumer_fills_cache_with_home_offset():
    """Cache stores GLOBAL NED (local telemetry + the drone's home offset)."""
    async def body():
        rt = SkyforgeRuntime(_make_show(n=2, homes=[(10.0, 20.0), (0.0, 0.0)]))
        # One packet per batch → the re-subscribe loop always re-sets the same value
        # (deterministic regardless of where the cancel lands in the cycle).
        drone = _FakeDrone([[_PV(1.5, 2.5, -5.2)]])
        await _consume_briefly(drone, rt, 0, settle=0.05)
        assert 0 in rt.current_positions and 0 in rt.current_velocities
        n, e, d = rt.current_positions[0]
        assert abs(n - (1.5 + 10.0)) < 1e-6   # + home N
        assert abs(e - (2.5 + 20.0)) < 1e-6   # + home E
        assert abs(d - (-5.2)) < 1e-6         # down is already global
    asyncio.run(body())


def test_telemetry_consumer_resubscribes_after_stream_ends():
    """A normally-ending stream is re-subscribed (the control loop must keep seeing
    fresh positions); the cache advances to the new batch."""
    async def body():
        rt = SkyforgeRuntime(_make_show(n=1))
        drone = _FakeDrone([[_PV(1.0, 0.0, -5.0)], [_PV(9.0, 0.0, -5.0)]])
        await _consume_briefly(drone, rt, 0, settle=0.05)
        assert abs(rt.current_positions[0][0] - 9.0) < 1e-6
    asyncio.run(body())


def test_telemetry_consumer_recovers_from_stream_error():
    """A dropped stream (exception) is caught and re-subscribed after the backoff —
    the documented MAVSDK hazard must not permanently strand telemetry."""
    async def body():
        rt = SkyforgeRuntime(_make_show(n=1))
        drone = _FakeDrone([[_PV(1.0, 0.0, -5.0), "RAISE"], [_PV(7.0, 0.0, -5.0)]])
        await _consume_briefly(drone, rt, 0, settle=0.7)   # > the 0.5 s backoff
        assert abs(rt.current_positions[0][0] - 7.0) < 1e-6
    asyncio.run(body())
