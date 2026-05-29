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
from .audio_beat import BeatDetector
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

_GZ_VISUAL_SVC  = "/world/default/visual_config"
_ARM_VISUALS    = ["5010_motor_base_0", "5010_motor_base_1",
                   "5010_motor_base_2", "5010_motor_base_3"]

# gz transport needs GZ_IP set to reach the sim's partition; without it the
# visual_config service call silently times out (no LED change).
_GZ_ENV = {**os.environ, "GZ_IP": "127.0.0.1"}


async def _led_update(drone_id: int, r: float, g: float, b: float) -> None:
    model = f"x500_{drone_id}"
    mat   = (
        f"material {{"
        f"ambient {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}} "
        f"diffuse {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}} "
        f"emissive {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}}"
        f"}}"
    )

    async def _send(vis: str) -> None:
        proto = f'name: "{model}::base_link::{vis}" {mat}'
        proc  = await asyncio.create_subprocess_exec(
            "gz", "service", "-s", _GZ_VISUAL_SVC,
            "--reqtype", "gz.msgs.Visual",
            "--reptype", "gz.msgs.Boolean",
            "--timeout", "200",
            "--req", proto,
            env=_GZ_ENV,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    await asyncio.gather(*(_send(v) for v in _ARM_VISUALS))
    # Spotlight intentionally off — arm lights provide all visual feedback


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
        # Sync target — number of drones actually flying. Defaults to the full
        # fleet; run_skyforge lowers it to the count that connected so a partial
        # fleet can still start (the show clock waits for ready_target, not n).
        self.ready_target = self.n_drones
        # Written by telemetry_consumer() each tick; read by the control loop + APF.
        self.current_positions:  dict[int, tuple[float, float, float]] = {}
        self.current_velocities: dict[int, tuple[float, float, float]] = {}
        # Envelopes are "computed" once the pipeline's compute_envelopes stage has
        # run; the ShowBuilder placeholder is all-zero radii. When computed, a
        # radius of 0.0 is a REAL constraint (no reactive room) and must clamp the
        # reactive offset to zero — NOT be treated as unbounded. Only genuinely
        # uncomputed (all-zero) envelopes fall back to unclamped, so a not-yet-
        # enveloped show can still preview reactive motion.
        self.envelopes_computed = any(
            seg.radius_m > 0.0
            for env in show.envelopes
            for seg in env.segments
        )
        self.beat = BeatDetector()
        self.beat.start()

    def reactive_offset(self, drone_id: int, show_time: float) -> tuple[float, float, float]:
        """Sum reactive offsets from all active bindings for this drone at show_time."""
        if not (0 <= drone_id < self.n_drones):
            return 0.0, 0.0, 0.0
        dN = dE = dD = 0.0
        for binding in self.show.reactive_bindings:
            if not (binding.t_start <= show_time <= binding.t_end):
                continue
            if binding.drone_ids and drone_id not in binding.drone_ids:
                continue
            env = self.show.envelopes[drone_id]
            radius = next(
                (s.radius_m for s in env.segments if s.t_start <= show_time <= s.t_end),
                None,
            )
            if radius is None:
                # No envelope segment covers this time → no defined safety budget.
                # Contribute nothing rather than assume unbounded freedom.
                continue
            if not self.envelopes_computed:
                # Placeholder (uncomputed) envelopes: preview reactive motion
                # unclamped. Computed envelopes use the radius as-is — a radius of
                # 0.0 correctly clamps the offset to zero (no reactive room).
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


# ── Telemetry consumer (cache filler) ─────────────────────────────────────────

async def telemetry_consumer(
    drone,
    drone_id:    int,
    runtime:     SkyforgeRuntime,
    abort_event: asyncio.Event,
) -> None:
    """Stream position/velocity into the runtime cache in its own task.

    CRITICAL: plain ``async for`` — NEVER wrapped in ``asyncio.wait_for``.
    Cancelling a pending ``__anext__()`` on a MAVSDK telemetry generator
    permanently breaks it (every later read raises ``StopAsyncIteration``), which
    is what previously stranded drones holding at altitude while formations never
    moved. The control loop only ever READS the cache this fills. Mirrors
    commander/dynamic_adapter.py:telemetry_consumer.
    """
    home   = runtime.show.drones[drone_id].home_ned
    hn, he = home.n, home.e
    while not abort_event.is_set():
        try:
            async for pv in drone.telemetry.position_velocity_ned():
                if abort_event.is_set():
                    return
                runtime.current_positions[drone_id] = (
                    pv.position.north_m + hn,
                    pv.position.east_m  + he,
                    pv.position.down_m,
                )
                runtime.current_velocities[drone_id] = (
                    pv.velocity.north_m_s,
                    pv.velocity.east_m_s,
                    pv.velocity.down_m_s,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Stream ended/errored — back off briefly and re-subscribe.
            await asyncio.sleep(0.5)


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
    if not (0 <= drone_id < runtime.n_drones):
        raise ValueError(
            f"drone_id {drone_id} out of range [0, {runtime.n_drones}) — malformed show?"
        )
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
    target = runtime.ready_target
    print(f"{tag} Ready ({ready_count[0]}/{target})")

    if ready_count[0] >= target:
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

    # ── Polynomial control loop (cache-based) ─────────────────────────────────
    # Telemetry is maintained by telemetry_consumer() in its own task; here we
    # only READ runtime.current_positions/current_velocities. This loop NEVER
    # wraps a telemetry __anext__ in wait_for — doing so permanently breaks the
    # MAVSDK stream and silently strands drones (see telemetry_consumer above and
    # commander/dynamic_adapter.py).
    prev_led_rgb = (-1.0, -1.0, -1.0)
    led_tick     = 0
    led_task: asyncio.Task | None = None

    while not abort_event.is_set():
        tick_start = time.monotonic()
        show_time  = tick_start - show_start_time[0]

        if show_time >= duration_s:
            print(f"{tag} Show complete at t={show_time:.1f}s")
            break

        # Nominal setpoint from polynomial (global NED) + reactive deviation
        nom = traj.evaluate(show_time)
        rdN, rdE, rdD = runtime.reactive_offset(drone_id, show_time)

        pos = runtime.current_positions.get(drone_id)
        if pos is None:
            # Telemetry not flowing yet — command nominal + reactive only (skip
            # APF since we lack our own position). Convert global → local.
            lN = (nom.n + rdN) - home.n
            lE = (nom.e + rdE) - home.e
            lD =  nom.d + rdD
        else:
            global_N, global_E, global_D = pos
            others_ned = [
                runtime.current_positions[j]
                for j in range(runtime.n_drones)
                if j != drone_id and j in runtime.current_positions
            ]
            own_vel = runtime.current_velocities.get(drone_id, (0.0, 0.0, 0.0))
            apf_dN, apf_dE, apf_dD = compute_apf_offset(
                (global_N, global_E, global_D), own_vel, others_ned, drone_id,
            )
            lN = (nom.n + rdN + apf_dN) - home.n
            lE = (nom.e + rdE + apf_dE) - home.e
            lD =  nom.d + rdD + apf_dD

        try:
            await drone.offboard.set_position_ned(PositionNedYaw(lN, lE, lD, 0.0))
        except Exception as exc:
            print(f"{tag} setpoint error (skipping tick): {exc}")

        # LED update at ~4 Hz — brightness driven by real-time audio beat energy.
        led_tick += 1
        if led_tick >= 3:
            led_tick = 0
            c          = led_track.evaluate(show_time)
            beat       = runtime.beat.beat_energy
            brightness = 0.15 + 0.85 * beat
            r = min(1.0, c.r * brightness)
            g = min(1.0, c.g * brightness)
            b = min(1.0, c.b * brightness)
            if beat > 0.8:
                flash = (beat - 0.8) / 0.2
                r = min(1.0, r + flash * 0.5)
                g = min(1.0, g + flash * 0.3)
                b = min(1.0, b + flash * 0.5)
            rgb = (r, g, b)
            if any(abs(rgb[k] - prev_led_rgb[k]) > 0.02 for k in range(3)):
                if led_task is None or led_task.done():
                    led_task = asyncio.create_task(_led_update(drone_id, r, g, b))
                    prev_led_rgb = rgb

        await asyncio.sleep(max(0.0, CONTROL_DT - (time.monotonic() - tick_start)))

    # ── Land ──────────────────────────────────────────────────────────────────
    # ── Land — staggered to prevent simultaneous descent collisions ───────────
    try:
        await drone.offboard.stop()
    except Exception:
        pass
    stagger_s = drone_id * 1.5
    if stagger_s > 0:
        print(f"{tag} Landing in {stagger_s:.1f}s (stagger)...")
        await asyncio.sleep(stagger_s)
    for attempt in range(3):
        try:
            await drone.action.land()
            print(f"{tag} Land command accepted")
            break
        except Exception as e:
            print(f"{tag} land() attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(2.0)
    try:
        async def _wait_disarm():
            async for armed in drone.telemetry.armed():
                if not armed:
                    return
        await asyncio.wait_for(_wait_disarm(), timeout=30.0)
        print(f"{tag} Disarmed.")
    except asyncio.TimeoutError:
        print(f"{tag} Disarm timeout — drone may still be in the air")
