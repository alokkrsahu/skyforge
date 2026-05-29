"""
DynamicRuntime and run_drone_commander for interactive fleet control.

Drones track live interpolated targets instead of pre-compiled polynomials.
Formation transitions use smoothstep easing for natural motion.
Supports multiple takeoff/land cycles within a single session.
"""
import asyncio
import math
import os
import time
from dataclasses import dataclass
from typing import Optional

from mavsdk.offboard import OffboardError, PositionNedYaw

from show.apf import compute_apf_offset
from show.config import CONTROL_DT, SHOW_ALT_M

_GZ_LIGHT_SVC = "/world/default/light_config"

# gz transport needs GZ_IP set to reach the sim's partition; without it the
# service call silently goes nowhere (no LED change).
_GZ_ENV = {**os.environ, "GZ_IP": "127.0.0.1"}

# The drones' visible "LEDs" are the four named arm-tip point lights. We recolor
# them via the light_config service (the render engine applies this at runtime —
# verified via a server-side camera). Each must be re-sent with its exact
# link-relative pose + attenuation, since light_config replaces the whole light.
# (model.sdf: pose x y 0.05, attenuation range 5 / 0.3 / 0.2 / 0.01)
_ARM_LIGHTS = {
    "light_front_left":  (0.174, 0.174, 0.05),
    "light_front_right": (0.174, -0.174, 0.05),
    "light_rear_left":  (-0.174, 0.174, 0.05),
    "light_rear_right": (-0.174, -0.174, 0.05),
}


def _ease_inout(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


@dataclass
class Transition:
    start_pos:  dict   # drone_id → (N, E, D)
    end_pos:    dict   # drone_id → (N, E, D)
    start_time: float
    duration_s: float


async def _led_update(drone_id: int, r: float, g: float, b: float) -> None:
    """Recolor the drone's four arm-tip point lights via the light_config service.

    The render engine applies this at runtime — verified via a server-side
    camera capture (a point light recolor tinted the scene). The previous
    visual_config call targeted the 5010_motor_base meshes, which have no SDF
    material and silently ignore overrides.

    macOS caveat: runtime color renders in camera feeds (e.g. the show_cam
    ImageDisplay panel), recordings, and a Linux GUI — but NOT in the live
    macOS GUI 3D view, which does not apply runtime light/material deltas.
    """
    model = f"x500_{drone_id}"

    async def _send(light: str, pos: tuple[float, float, float]) -> None:
        x, y, z = pos
        req = (
            f'name: "{model}::base_link::{light}" type: POINT '
            f'diffuse {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}} '
            f'specular {{r:{r * 0.3:.3f} g:{g * 0.3:.3f} b:{b * 0.3:.3f} a:1}} '
            f'pose {{position {{x:{x} y:{y} z:{z}}}}} '
            f'range: 5.0 attenuation_constant: 0.3 attenuation_linear: 0.2 '
            f'attenuation_quadratic: 0.01 cast_shadows: false intensity: 1.0'
        )
        proc = await asyncio.create_subprocess_exec(
            "gz", "service", "-s", _GZ_LIGHT_SVC,
            "--reqtype", "gz.msgs.Light",
            "--reptype", "gz.msgs.Boolean",
            "--timeout", "300",
            "--req", req,
            env=_GZ_ENV,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    await asyncio.gather(*(_send(l, p) for l, p in _ARM_LIGHTS.items()))


async def telemetry_consumer(
    drone,
    drone_id:    int,
    runtime:     "DynamicRuntime",
    hn:          float,
    he:          float,
    abort_event: asyncio.Event,
) -> None:
    """Continuously stream position/velocity into the runtime cache.

    CRITICAL: uses a plain ``async for`` and is NEVER wrapped in
    ``asyncio.wait_for``. Cancelling a pending ``__anext__()`` on a MAVSDK
    telemetry generator permanently breaks it — every later read raises
    ``StopAsyncIteration`` (verified). The old control loop wrapped
    ``position_velocity_ned().__anext__()`` in ``wait_for(timeout=0.5)``; under
    multi-drone load the first timeout silently killed each drone's telemetry,
    so the control loop fell back to holding at home forever and formations
    (star/circle/…) never moved. Here telemetry lives in its own task and the
    control loop only ever reads the cache it fills.
    """
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


async def led_watcher(runtime: "DynamicRuntime", abort_event: asyncio.Event) -> None:
    """Push the fleet LED colour to Gazebo whenever it changes.

    Runs as its own task — deliberately NOT inside the per-drone control loop.
    Each `_led_update` spawns four `gz` CLI subprocesses; doing that every tick
    for every drone (the old design) flooded the event loop and starved the
    offboard setpoint stream, dropping drones out of offboard. Here we send one
    batch only when `runtime.led_color` actually changes, so the flight loop
    never competes with the light subprocesses.
    """
    last_sent: Optional[tuple[float, float, float]] = None
    while not abort_event.is_set():
        color = runtime.led_color
        if color != last_sent and runtime.current_positions:
            last_sent = color
            r, g, b = color
            try:
                await asyncio.gather(
                    *(_led_update(i, r, g, b) for i in range(runtime.n_drones))
                )
            except Exception:
                pass   # a transient gz/service hiccup must never kill the watcher
        await asyncio.sleep(0.2)


class DynamicRuntime:
    def __init__(self, n_drones: int, home_ned_list: list[tuple[float, float]]):
        self.n_drones         = n_drones
        self.home_ned         = {i: home_ned_list[i] for i in range(n_drones)}
        self.formation_center = (
            sum(h[0] for h in home_ned_list) / n_drones,
            sum(h[1] for h in home_ned_list) / n_drones,
        )
        self.current_positions:  dict[int, tuple[float, float, float]] = {}
        self.current_velocities: dict[int, tuple[float, float, float]] = {}
        self.transition: Optional[Transition] = None
        self.hold_pos:   dict[int, tuple[float, float, float]] = {
            i: (home_ned_list[i][0], home_ned_list[i][1], -SHOW_ALT_M)
            for i in range(n_drones)
        }
        self.alt_m        = float(SHOW_ALT_M)
        self.led_color    = (0.0, 0.8, 0.0)
        self.airborne     = False
        self.abort_flag   = False
        # Multi-flight support: flight_cycle increments each takeoff call,
        # waking drone coroutines that are waiting for the next flight.
        self.flight_cycle = 0
        self.ready_count  = 0   # drones that entered offboard this flight
        self.ready_target = 0   # set from main() after connect

    def target_ned(self, drone_id: int, now: float) -> tuple[float, float, float]:
        if self.transition is None:
            return self.hold_pos[drone_id]
        t     = self.transition
        alpha = min(1.0, (now - t.start_time) / t.duration_s)
        alpha = _ease_inout(alpha)
        s     = t.start_pos[drone_id]
        e     = t.end_pos[drone_id]
        pos   = (
            s[0] + (e[0] - s[0]) * alpha,
            s[1] + (e[1] - s[1]) * alpha,
            s[2] + (e[2] - s[2]) * alpha,
        )
        if alpha >= 1.0:
            self.transition = None
        return pos

    def start_transition(
        self,
        end_pos:    dict[int, tuple[float, float, float]],
        duration_s: float,
    ) -> None:
        now       = time.monotonic()
        start_pos = {}
        for i in range(self.n_drones):
            start_pos[i] = self.current_positions.get(
                i,
                self.hold_pos.get(i, (self.home_ned[i][0], self.home_ned[i][1], -self.alt_m)),
            )
        self.transition = Transition(
            start_pos  = start_pos,
            end_pos    = end_pos,
            start_time = now,
            duration_s = duration_s,
        )
        self.hold_pos.update(end_pos)


async def run_drone_commander(
    drone_id:    int,
    drone,
    runtime:     DynamicRuntime,
    abort_event: asyncio.Event,
) -> None:
    tag    = f"[drone {drone_id}]"
    hn, he = runtime.home_ned[drone_id]
    last_cycle = 0   # starts at 0; wait until flight_cycle > 0 (i.e. takeoff was typed)

    while not abort_event.is_set():
        # ── Wait for next takeoff ─────────────────────────────────────────────
        while not abort_event.is_set() and runtime.flight_cycle <= last_cycle:
            await asyncio.sleep(0.1)
        if abort_event.is_set():
            break
        last_cycle = runtime.flight_cycle

        # Stagger arm/takeoff across the fleet. All drone coroutines are released
        # together when 'takeoff' is typed; if every one calls arm()+takeoff() in
        # the same instant, the burst of concurrent gRPC + MAVLink traffic
        # overwhelms the mavsdk_servers under load and they crash ("Socket closed"
        # / "Connection reset by peer"), taking down the whole fleet. Spreading
        # the burst by drone_id keeps the peak survivable.
        await asyncio.sleep(drone_id * 0.3)
        if abort_event.is_set():
            break

        # ── Arm & takeoff ─────────────────────────────────────────────────────
        print(f"{tag} Arming...")
        try:
            await drone.action.arm()
            await drone.action.takeoff()
        except Exception as e:
            print(f"{tag} arm/takeoff failed: {e} — skipping this cycle")
            continue

        try:
            async def _wait_in_air():
                async for in_air in drone.telemetry.in_air():
                    if in_air:
                        return
            await asyncio.wait_for(_wait_in_air(), timeout=30.0)
            print(f"{tag} Airborne")
        except asyncio.TimeoutError:
            print(f"{tag} in_air timeout")
            continue

        await asyncio.sleep(4.0)

        # ── Enter offboard mode ───────────────────────────────────────────────
        for _ in range(3):
            await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, -runtime.alt_m, 0.0))
            await asyncio.sleep(0.2)

        offboard_ok = False
        for attempt in range(3):
            try:
                await drone.offboard.start()
                offboard_ok = True
                print(f"{tag} Offboard active (attempt {attempt + 1})")
                break
            except OffboardError as e:
                print(f"{tag} offboard attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(1.5)
                    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, -runtime.alt_m, 0.0))

        if not offboard_ok:
            print(f"{tag} failed to enter offboard — skipping this cycle")
            continue

        # ── Sync: wait for all active drones to enter offboard ────────────────
        runtime.ready_count += 1
        target = runtime.ready_target if runtime.ready_target > 0 else runtime.n_drones
        print(f"{tag} Ready ({runtime.ready_count}/{target})")
        if runtime.ready_count >= target:
            runtime.airborne = True
            print("[commander] All drones in offboard — live control active")

        # ── Unified control loop (cache-based) ────────────────────────────────
        # Telemetry is maintained by telemetry_consumer() in its own task; here
        # we only READ runtime.current_positions. This loop runs on a steady
        # CONTROL_DT timer and never blocks on (or cancels) a telemetry stream,
        # which is what previously stranded drones in hold-at-home.
        was_airborne   = False
        sync_deadline  = time.monotonic() + 45.0

        while not abort_event.is_set():
            tick_start = time.monotonic()
            pos = runtime.current_positions.get(drone_id)

            # ── Hold or live? ─────────────────────────────────────────────────
            if not runtime.airborne:
                if was_airborne:
                    break   # land() received
                if time.monotonic() > sync_deadline and runtime.ready_count > 0:
                    runtime.airborne = True
                    print(f"[commander] Sync timeout — starting with {runtime.ready_count} drones")
                    continue
                try:
                    await drone.offboard.set_position_ned(
                        PositionNedYaw(0.0, 0.0, -runtime.alt_m, 0.0))
                except Exception as exc:
                    print(f"{tag} hold setpoint error: {exc}")
                await asyncio.sleep(max(0.0, CONTROL_DT - (time.monotonic() - tick_start)))
                continue

            # ── Live control ──────────────────────────────────────────────────
            was_airborne = True
            target_N, target_E, target_D = runtime.target_ned(drone_id, tick_start)

            if pos is None:
                # Telemetry not flowing yet — still command the formation target
                # (minus home offset); skip APF since we lack our own position.
                lN, lE, lD = target_N - hn, target_E - he, target_D
            else:
                global_N, global_E, global_D = pos
                others = [
                    runtime.current_positions[j]
                    for j in range(runtime.n_drones)
                    if j != drone_id and j in runtime.current_positions
                ]
                own_vel = runtime.current_velocities.get(drone_id, (0.0, 0.0, 0.0))
                apf_dN, apf_dE, apf_dD = compute_apf_offset(
                    (global_N, global_E, global_D), own_vel, others, drone_id,
                )
                lN = (target_N + apf_dN) - hn
                lE = (target_E + apf_dE) - he
                lD =  target_D + apf_dD

            try:
                await drone.offboard.set_position_ned(PositionNedYaw(lN, lE, lD, 0.0))
            except Exception as exc:
                print(f"{tag} setpoint error: {exc}")

            await asyncio.sleep(max(0.0, CONTROL_DT - (time.monotonic() - tick_start)))

        # ── Land ──────────────────────────────────────────────────────────────
        try:
            await drone.offboard.stop()
        except Exception:
            pass

        stagger_s = 0.0 if runtime.abort_flag else drone_id * 0.5
        if stagger_s > 0:
            print(f"{tag} Landing in {stagger_s:.1f}s...")
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
            print(f"{tag} Disarmed")
        except asyncio.TimeoutError:
            print(f"{tag} Disarm timeout")

        # Clear per-flight telemetry so stale positions don't affect next flight
        runtime.current_positions.pop(drone_id, None)
        runtime.current_velocities.pop(drone_id, None)

        if runtime.abort_flag:
            break   # abort: stop cycling
        # else: loop back to wait for next takeoff command
