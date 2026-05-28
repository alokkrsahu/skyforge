"""
Skyforge runtime adapter.

Plays a ShowFile (piecewise polynomial trajectories) via MAVSDK.
Replaces ShowCoordinator + BezierSegment: no barriers, no convergence checks.
APF collision avoidance is retained for safety; reactive bindings use synthetic
input signals for simulation.

Flow per drone:
  arm → takeoff → offboard (hold at show altitude) → sync barrier →
  polynomial loop (evaluate traj + APF + reactive each tick) → land
"""
import asyncio
import math
import os
import sys
import time

from mavsdk.offboard import OffboardError, PositionNedYaw

from .apf import compute_apf_offset
from .config import CONTROL_DT, SHOW_ALT_M

# Add the skyforge package to sys.path so core.* and compiler.* are importable
# whether this module is imported from run_skyforge.py or directly.
_SKYFORGE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _SKYFORGE_PATH not in sys.path:
    sys.path.insert(0, _SKYFORGE_PATH)

from core.show_format.schema import ShowFile
from core.reactive.primitives import evaluate as reactive_evaluate

# The polynomial's first TAKEOFF_OFFSET_S seconds encode the takeoff phase.
# After action.takeoff() + stabilisation the drone is already at show altitude,
# so we jump to this offset when the polynomial loop begins.
TAKEOFF_OFFSET_S = 15.0

_GZ_LIGHT_TOPIC = "/world/default/light_config"
_GZ_LIGHT_TYPE  = "gz.msgs.Light"


# ── Async LED update (avoids fork() + gRPC thread conflict) ──────────────────

async def _led_update(drone_id: int, r: float, g: float, b: float) -> None:
    """Send LED colour to Gazebo via asyncio subprocess (posix_spawn, not fork)."""
    model = f"x500_{drone_id}"
    sr, sg, sb = r * 0.3, g * 0.3, b * 0.3
    arm_lights = [
        "light_front_left", "light_front_right",
        "light_rear_left",  "light_rear_right",
    ]
    for light_name in arm_lights:
        name  = f"{model}::base_link::{light_name}"
        proto = (
            f'name: "{name}" type: POINT '
            f'diffuse {{r: {r} g: {g} b: {b} a: 1.0}} '
            f'specular {{r: {sr:.3f} g: {sg:.3f} b: {sb:.3f} a: 1.0}} '
            f'range: 5.0 attenuation_constant: 0.3 attenuation_linear: 0.2 '
            f'attenuation_quadratic: 0.01 intensity: 1.0 is_light_off: false'
        )
        proc = await asyncio.create_subprocess_exec(
            "gz", "topic", "-t", _GZ_LIGHT_TOPIC, "-m", _GZ_LIGHT_TYPE, "-p", proto,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    # Spotlight (downward-facing)
    name   = f"{model}::base_link::spotlight_down"
    sr2, sg2, sb2 = r * 0.5, g * 0.5, b * 0.5
    proto  = (
        f'name: "{name}" type: SPOT '
        f'diffuse {{r: {r} g: {g} b: {b} a: 1.0}} '
        f'specular {{r: {sr2:.3f} g: {sg2:.3f} b: {sb2:.3f} a: 1.0}} '
        f'direction {{x: 0.0 y: 0.0 z: -1.0}} range: 15.0 '
        f'attenuation_constant: 0.3 attenuation_linear: 0.05 '
        f'attenuation_quadratic: 0.001 spot_inner_angle: 0.3 '
        f'spot_outer_angle: 0.8 spot_falloff: 1.0 intensity: 1.0 is_light_off: false'
    )
    proc = await asyncio.create_subprocess_exec(
        "gz", "topic", "-t", _GZ_LIGHT_TOPIC, "-m", _GZ_LIGHT_TYPE, "-p", proto,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


# ── Input signal synthesis ────────────────────────────────────────────────────

def _synthetic_input(
    input_source: str,
    params:       dict,
    show_time:    float,
    drone_id:     int = 0,
) -> float:
    """Generate a normalised [0,1] input signal for a reactive binding."""
    if input_source == "music_beat":
        bpm          = params.get("bpm", 120.0)
        phase_offset = params.get("phase_per_drone", 0.0) * drone_id
        phase        = (show_time * bpm / 60.0 + phase_offset) % 1.0
        return max(0.0, math.sin(math.pi * phase))   # smooth pulse per beat
    if input_source == "audio_energy":
        return 0.3 + 0.3 * math.sin(2 * math.pi * show_time / 4.0)
    return 0.0


# ── Runtime state holder ──────────────────────────────────────────────────────

class SkyforgeRuntime:
    def __init__(self, show: ShowFile):
        self.show     = show
        self.n_drones = len(show.drones)
        # Written by drone coroutines each tick; read by APF computation
        self.current_positions: dict[int, tuple[float, float, float]] = {}

    def reactive_offset(self, drone_id: int, show_time: float) -> tuple[float, float, float]:
        """Sum reactive offsets from all active bindings for this drone at show_time."""
        dN = dE = dD = 0.0
        for binding in self.show.reactive_bindings:
            if not (binding.t_start <= show_time <= binding.t_end):
                continue
            if binding.drone_ids and drone_id not in binding.drone_ids:
                continue
            env = self.show.envelopes[drone_id]
            radius = next(
                (s.radius_m for s in env.segments if s.t_start <= show_time <= s.t_end),
                0.0,
            )
            # radius_m=0.0 means safety envelope not yet computed (Phase 2).
            # Use float('inf') so Phase 1 shows reactive effects unclamped.
            if radius == 0.0:
                radius = float("inf")
            iv = _synthetic_input(binding.input_source, binding.parameters, show_time, drone_id)
            odN, odE, odD = reactive_evaluate(
                binding.primitive, binding.parameters, iv, show_time, radius
            )
            dN += odN; dE += odE; dD += odD
        return dN, dE, dD


# ── Hold helper (keeps offboard alive during sync wait) ──────────────────────

async def _hold_at_altitude(drone, down_m: float):
    """Continuously resend a hover setpoint so offboard mode stays active."""
    while True:
        await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, down_m, 0.0))
        await asyncio.sleep(0.05)


# ── Per-drone coroutine ───────────────────────────────────────────────────────

async def run_drone_skyforge(
    drone_id: int,
    drone,                         # mavsdk.System
    runtime: SkyforgeRuntime,
    show_start_event: asyncio.Event,
    abort_event: asyncio.Event,    # set by any drone that fails pre-show
    show_start_time: list,         # [float | None] — set by the last-ready drone
    ready_count: list,             # [int] — incremented as each drone becomes ready
):
    tag        = f"[drone {drone_id}]"
    home       = runtime.show.drones[drone_id].home_ned
    traj       = runtime.show.trajectories[drone_id]
    led_track  = runtime.show.led_tracks[drone_id]
    duration_s = runtime.show.metadata.duration_s

    def _abort(reason: str):
        print(f"{tag} ABORT: {reason} — signalling all drones")
        abort_event.set()

    # ── Arm & takeoff ─────────────────────────────────────────────────────────
    print(f"{tag} Arming...")
    await drone.action.arm()
    print(f"{tag} Armed. Taking off...")
    await drone.action.takeoff()

    print(f"{tag} Waiting for in-air...")
    try:
        async def _wait_in_air():
            async for in_air in drone.telemetry.in_air():
                if in_air:
                    return
        await asyncio.wait_for(_wait_in_air(), timeout=30.0)
        print(f"{tag} Airborne")
    except asyncio.TimeoutError:
        _abort("in_air timeout after 30 s")
        return

    await asyncio.sleep(4.0)   # let altitude stabilise near show_alt

    # ── Enter offboard mode ───────────────────────────────────────────────────
    # Send one setpoint first — PX4 requires at least one before start().
    print(f"{tag} Entering offboard mode...")
    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, -SHOW_ALT_M, 0.0))
    await asyncio.sleep(0.5)

    offboard_ok = False
    for attempt in range(3):
        try:
            await drone.offboard.start()
            offboard_ok = True
            print(f"{tag} Offboard mode active (attempt {attempt + 1})")
            break
        except OffboardError as e:
            print(f"{tag} offboard.start() attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(1.5)
                await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, -SHOW_ALT_M, 0.0))

    if not offboard_ok:
        _abort("could not enter offboard after 3 attempts")
        try:
            await drone.action.land()
        except Exception:
            pass
        return

    # Keep sending hold setpoints while waiting for all drones to sync.
    # Without this, PX4 exits offboard mode after ~500 ms of silence.
    hold_task = asyncio.create_task(_hold_at_altitude(drone, -SHOW_ALT_M))

    # ── Synchronise show clock ────────────────────────────────────────────────
    ready_count[0] += 1
    print(f"{tag} Ready ({ready_count[0]}/{runtime.n_drones})")

    if ready_count[0] >= runtime.n_drones:
        # Last drone ready: record shared start time and fire the event.
        show_start_time[0] = time.monotonic() - TAKEOFF_OFFSET_S
        show_start_event.set()
        print("[skyforge] All drones ready — polynomial show starting")
    else:
        # Wait for show start or abort signal (120 s safety timeout).
        show_fut  = asyncio.ensure_future(show_start_event.wait())
        abort_fut = asyncio.ensure_future(abort_event.wait())
        done, pending = await asyncio.wait(
            [show_fut, abort_fut],
            timeout=120.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()

        if abort_event.is_set():
            print(f"{tag} Abort signal received while waiting — landing")
            hold_task.cancel()
            try:
                await hold_task
            except asyncio.CancelledError:
                pass
            try:
                await drone.action.land()
            except Exception:
                pass
            return

        if not done:   # timeout
            _abort("show_start timeout — not all drones became ready within 120 s")
            hold_task.cancel()
            try:
                await hold_task
            except asyncio.CancelledError:
                pass
            try:
                await drone.action.land()
            except Exception:
                pass
            return

    hold_task.cancel()
    try:
        await hold_task
    except asyncio.CancelledError:
        pass

    # ── Polynomial control loop ───────────────────────────────────────────────
    pos_stream   = drone.telemetry.position_velocity_ned().__aiter__()
    prev_led_rgb = (-1.0, -1.0, -1.0)
    led_tick     = 0

    while True:
        tick_start = time.monotonic()
        show_time  = tick_start - show_start_time[0]

        if show_time >= duration_s:
            print(f"{tag} Show complete at t={show_time:.1f}s")
            break

        # Read telemetry
        try:
            pv = await asyncio.wait_for(pos_stream.__anext__(), timeout=0.5)
        except (asyncio.TimeoutError, StopAsyncIteration):
            await asyncio.sleep(CONTROL_DT)
            continue

        # Convert local → global NED
        global_N = pv.position.north_m + home.n
        global_E = pv.position.east_m  + home.e
        runtime.current_positions[drone_id] = (global_N, global_E, pv.position.down_m)

        # Nominal setpoint from polynomial (global NED)
        nom = traj.evaluate(show_time)

        # Reactive deviation
        rdN, rdE, rdD = runtime.reactive_offset(drone_id, show_time)

        # APF repulsion
        others_ne = [
            (runtime.current_positions[j][0], runtime.current_positions[j][1])
            for j in range(runtime.n_drones)
            if j != drone_id and j in runtime.current_positions
        ]
        apf_dN, apf_dE = compute_apf_offset((global_N, global_E), others_ne, drone_id)

        # Convert global → local NED and send setpoint
        lN = (nom.n + rdN + apf_dN) - home.n
        lE = (nom.e + rdE + apf_dE) - home.e
        lD = nom.d + rdD

        await drone.offboard.set_position_ned(PositionNedYaw(lN, lE, lD, 0.0))

        # LED update at ~1 Hz — fire-and-forget async subprocess (no fork).
        led_tick += 1
        if led_tick >= 10:
            led_tick = 0
            c   = led_track.evaluate(show_time)
            rgb = (c.r, c.g, c.b)
            if any(abs(rgb[k] - prev_led_rgb[k]) > 0.05 for k in range(3)):
                print(f"{tag} LED → r={c.r:.2f} g={c.g:.2f} b={c.b:.2f}  t={show_time:.0f}s")
                asyncio.create_task(_led_update(drone_id, c.r, c.g, c.b))
                prev_led_rgb = rgb

        await asyncio.sleep(max(0.0, CONTROL_DT - (time.monotonic() - tick_start)))

    # ── Land ──────────────────────────────────────────────────────────────────
    try:
        await drone.offboard.stop()
    except Exception:
        pass
    await drone.action.land()
    async for armed in drone.telemetry.armed():
        if not armed:
            print(f"{tag} Disarmed.")
            break
