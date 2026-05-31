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
from show.led_backend import make_led_backend

# LED control is delegated to a pluggable backend (Gazebo for SITL, a stub/driver
# for hardware). ONE module-level instance → its concurrency semaphore is shared
# fleet-wide (a per-drone backend would spawn ~N×16 `gz` procs and starve the
# offboard setpoint stream).
_LED = make_led_backend("commander")


def _ease_inout(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


@dataclass
class Transition:
    start_pos:  dict   # drone_id → (N, E, D)
    end_pos:    dict   # drone_id → (N, E, D)
    start_time: float
    duration_s: float


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
                runtime.position_timestamps[drone_id] = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Stream ended/errored — back off briefly and re-subscribe.
            await asyncio.sleep(0.5)


# ── Mid-show resilience: detect & handle a drone whose telemetry goes stale ─────

DROPOUT_TIMEOUT_S = 2.0   # no telemetry for this long (while airborne) → "lost"


def apply_health_policy(
    runtime: "DynamicRuntime", now: float, timeout: float = DROPOUT_TIMEOUT_S,
) -> list[int]:
    """Detect drones whose telemetry has gone stale and act per runtime.fail_mode.
    Returns the list of lost drone ids (empty when none / not airborne).

      * "abort"    — any loss triggers a fleet emergency land (abort_flag + airborne off).
      * "continue" — drop the lost drone(s) from the live caches so APF and the fleet
                     sync stop chasing a ghost; the show goes on with the survivors.

    Only acts while airborne (a drone that never came up is handled by the sync barrier).
    """
    if not runtime.airborne:
        return []
    lost = [i for i, ts in list(runtime.position_timestamps.items()) if now - ts > timeout]
    if not lost:
        return []
    if runtime.fail_mode == "abort":
        runtime.abort_flag = True
        runtime.airborne   = False
    else:   # graceful degradation
        for i in lost:
            runtime.current_positions.pop(i, None)
            runtime.current_velocities.pop(i, None)
            runtime.position_timestamps.pop(i, None)
    return lost


async def monitor_fleet_health(
    runtime: "DynamicRuntime", abort_event: asyncio.Event,
    interval: float = 1.0, timeout: float = DROPOUT_TIMEOUT_S,
    black_box=None, abort_policy=None, health_q=None,
) -> None:
    """Background task: once per `interval`, apply the dropout health policy and log.

    If `black_box`/`abort_policy` are supplied (run_commander wires them from env), also
    record a per-tick fleet summary and trigger an automatic abort on a policy breach."""
    from show.fleet_monitor import DroneHealth, summarize, should_auto_abort
    while not abort_event.is_set():
        now  = time.monotonic()
        lost = apply_health_policy(runtime, now, timeout)
        if lost:
            action = "ABORTING" if runtime.fail_mode == "abort" else "continuing without them"
            print(f"[monitor] Lost drones {lost} (no telemetry > {timeout:.0f}s) — {action}")

        if black_box is not None or abort_policy is not None or health_q is not None:
            healths = []
            for i in range(runtime.n_drones):
                pos = runtime.current_positions.get(i)
                age = now - runtime.position_timestamps.get(i, now - 1e9)
                err = None
                if pos is not None:
                    tN, tE, tD = runtime.peek_target(i, now)   # read-only (never clear a transition)
                    err = ((pos[0] - tN) ** 2 + (pos[1] - tE) ** 2 + (pos[2] - tD) ** 2) ** 0.5
                healths.append(DroneHealth(drone_id=i, armed=runtime.airborne, age_s=age, pos_error_m=err))
            summary = summarize(healths, runtime.n_drones, stale_age_s=timeout)
            if black_box is not None:
                black_box.record({"t": now, "n_seen": summary.n_seen, "n_lost": summary.n_lost,
                                  "max_pos_error_m": summary.max_pos_error_m,
                                  "min_battery_frac": summary.min_battery_frac})
            if health_q is not None:                           # UI dashboard fan-out (latest-wins)
                rec = {"type": "health", "n_total": summary.n_total, "n_seen": summary.n_seen,
                       "n_lost": summary.n_lost, "min_battery_frac": summary.min_battery_frac,
                       "max_pos_error_m": summary.max_pos_error_m, "anomalies": summary.anomalies}
                try:
                    health_q.put_nowait(rec)
                except Exception:
                    try:
                        health_q.get_nowait(); health_q.put_nowait(rec)   # drop oldest
                    except Exception:
                        pass
            if abort_policy is not None and runtime.airborne:
                fire, why = should_auto_abort(summary, abort_policy)
                if fire:
                    print(f"[monitor] AUTO-ABORT — {why}")
                    runtime.abort_flag = True
                    runtime.airborne   = False
        await asyncio.sleep(interval)


async def led_watcher(runtime: "DynamicRuntime", abort_event: asyncio.Event) -> None:
    """Push the fleet LED colour to Gazebo whenever it changes.

    Runs as its own task — deliberately NOT inside the per-drone control loop.
    Each `set_led` spawns four `gz` CLI subprocesses (Gazebo backend); doing that
    every tick for every drone (the old design) flooded the event loop and starved
    the offboard setpoint stream, dropping drones out of offboard. Here we send one
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
                    *(_LED.set_led(i, r, g, b) for i in range(runtime.n_drones))
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
        # Mid-show resilience: last telemetry wall-clock per drone (stamped by
        # telemetry_consumer). A drone whose telemetry goes stale is "lost"; the
        # fail_mode decides what the fleet does about it.
        self.position_timestamps: dict[int, float] = {}
        self.fail_mode = os.environ.get("SKYFORGE_FAIL_MODE", "continue").lower()

    def target_ned(self, drone_id: int, now: float) -> tuple[float, float, float]:
        if self.transition is None:
            return self.hold_pos[drone_id]
        t     = self.transition
        # Clamp to [0,1]: alpha<0 (a scheduled future start_time) holds at start_pos
        # until T0; alpha>1 holds at end_pos.
        alpha = max(0.0, min(1.0, (now - t.start_time) / t.duration_s))
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

    def peek_target(self, drone_id: int, now: float) -> tuple[float, float, float]:
        """Like target_ned but WITHOUT the finished-transition clear — pure/read-only,
        safe to call OFF the control tick (e.g. the web snapshot push path). target_ned
        mutates (`self.transition = None` at alpha>=1); the UI must not trigger that."""
        if self.transition is None:
            return self.hold_pos[drone_id]
        t = self.transition
        alpha = _ease_inout(max(0.0, min(1.0, (now - t.start_time) / t.duration_s)))
        s, e = t.start_pos[drone_id], t.end_pos[drone_id]
        return (s[0] + (e[0] - s[0]) * alpha,
                s[1] + (e[1] - s[1]) * alpha,
                s[2] + (e[2] - s[2]) * alpha)

    def start_transition(
        self,
        end_pos:    dict[int, tuple[float, float, float]],
        duration_s: float,
        start_at:   Optional[float] = None,
    ) -> None:
        """Begin a move. ``start_at`` (a ``time.monotonic()`` deadline) schedules a
        SYNCHRONIZED future start — drones hold at start_pos until then, so multiple
        controllers/agents fed the same T0 begin as one. Default = start now."""
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
            start_time = now if start_at is None else start_at,
            duration_s = duration_s,
        )
        self.hold_pos.update(end_pos)


async def _ensure_ready(drone, timeout: float = 20.0) -> bool:
    """True once the autopilot link is up and the EKF has global + home position.

    The connect-time readiness gate goes stale under load: a PX4->server heartbeat
    gap transiently drops the system, and arming a disconnected system makes
    mavsdk_server *abort* (std::bad_optional_access in its lazy Action plugin)
    rather than return an error — which kills the server and surfaces to us as
    "Connection reset by peer". So re-confirm health immediately before every arm.

    Follows _wait_healthy's established pattern: wait_for around a *fresh* health()
    stream. The generator is discarded afterwards, so the 'never wait_for a
    telemetry stream you keep using' rule doesn't apply here."""
    async def _wait():
        async for h in drone.telemetry.health():
            if h.is_global_position_ok and h.is_home_position_ok:
                return True
        return False
    try:
        return await asyncio.wait_for(_wait(), timeout=timeout)
    except Exception:   # TimeoutError, or a gRPC error if the server is down
        return False


async def run_drone_commander(
    drone_id:    int,
    drone,
    runtime:     DynamicRuntime,
    abort_event: asyncio.Event,
    respawn_server=None,
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

        # ── Arm & takeoff (crash-hardened) ────────────────────────────────────
        # mavsdk_server aborts if arm() reaches a momentarily-disconnected system
        # (bad_optional_access in its lazy Action plugin), so:
        #  1. gate every arm on a FRESH readiness check (link up + EKF), not the
        #     possibly-stale connect-time gate;
        #  2. if the link is down (or a server died mid-arm), respawn that server
        #     and retry — the old code just "skipped the cycle", leaving the
        #     server dead so every later takeoff failed too.
        armed = False
        for attempt in range(3):
            if abort_event.is_set():
                break
            ready = await _ensure_ready(drone)
            if not ready and respawn_server is not None:
                print(f"{tag} link down pre-arm — respawning server (attempt {attempt + 1})")
                await respawn_server(drone_id)
                ready = await _ensure_ready(drone, timeout=25.0)
            if not ready:
                continue
            print(f"{tag} Arming...")
            try:
                await drone.action.arm()
                await drone.action.takeoff()
                armed = True
                break
            except Exception as e:
                print(f"{tag} arm/takeoff failed (attempt {attempt + 1}): {e}")
                # A throwing arm usually means the server just aborted — respawn
                # so the next attempt has a live server to talk to.
                if respawn_server is not None and attempt < 2:
                    await respawn_server(drone_id)
        if not armed:
            print(f"{tag} arm/takeoff failed — skipping this cycle")
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
