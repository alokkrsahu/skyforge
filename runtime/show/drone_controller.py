"""Per-drone async coroutine: arm → takeoff → offboard show loop → land."""
import asyncio
import time
from typing import Tuple

from mavsdk import System
from mavsdk.offboard import OffboardError, PositionNedYaw

from .apf import compute_apf_offset
from .config import (
    CONTROL_DT, DRONE_HOMES, N_DRONES,
    SHOW_ALT_M, TAKEOFF_ALT_M,
)
from .coordinator import ShowCoordinator


def _global_to_local(gN: float, gE: float, drone_id: int) -> Tuple[float, float]:
    hN, hE = DRONE_HOMES[drone_id]
    return (gN - hN, gE - hE)


def _local_to_global(lN: float, lE: float, drone_id: int) -> Tuple[float, float]:
    hN, hE = DRONE_HOMES[drone_id]
    return (lN + hN, lE + hE)


async def run_drone(drone_id: int, drone: System, coordinator: ShowCoordinator):
    tag = f"[drone {drone_id}]"

    # ── Arm ──────────────────────────────────────────────────────────────────
    print(f"{tag} Arming...")
    await drone.action.arm()

    # ── Takeoff ───────────────────────────────────────────────────────────────
    print(f"{tag} Taking off...")
    await drone.action.takeoff()

    # Wait until actually in the air
    async for in_air in drone.telemetry.in_air():
        if in_air:
            print(f"{tag} Airborne")
            break

    # Brief pause to let altitude stabilise
    await asyncio.sleep(3.0)

    # ── Enter offboard mode ───────────────────────────────────────────────────
    # PX4 requires at least one setpoint before start()
    hold_down = -TAKEOFF_ALT_M
    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, hold_down, 0.0))
    await asyncio.sleep(0.2)
    try:
        await drone.offboard.start()
        print(f"{tag} Offboard mode active")
    except OffboardError as e:
        print(f"{tag} ERROR: offboard.start() failed: {e}")
        await drone.action.land()
        return

    # Open a persistent telemetry stream (not re-created each tick)
    pos_stream = drone.telemetry.position_velocity_ned().__aiter__()

    last_segment_id = coordinator.segment_id
    segment_start_time = time.monotonic()

    # ── Main control loop ─────────────────────────────────────────────────────
    while not coordinator.show_complete:
        tick_start = time.monotonic()

        # 1. Read current position
        try:
            pv = await asyncio.wait_for(pos_stream.__anext__(), timeout=0.5)
        except (asyncio.TimeoutError, StopAsyncIteration):
            await asyncio.sleep(CONTROL_DT)
            continue

        local_N  = pv.position.north_m
        local_E  = pv.position.east_m
        local_down = pv.position.down_m

        # 2. Convert to global NED and publish to coordinator
        global_N, global_E = _local_to_global(local_N, local_E, drone_id)
        global_pos = (global_N, global_E, local_down)
        coordinator.current_positions[drone_id] = global_pos
        coordinator.barrier.update_position(drone_id, global_pos)

        # 3. Trigger barrier check (safe: asyncio is single-threaded)
        await coordinator.tick()

        # 4. Detect segment change
        if coordinator.segment_id != last_segment_id:
            last_segment_id = coordinator.segment_id
            segment_start_time = time.monotonic()

        # 5. Compute nominal target
        bezier = coordinator.current_bezier.get(drone_id)
        if bezier is not None:
            elapsed = time.monotonic() - segment_start_time
            nom_gN, nom_gE = bezier.position_at(elapsed)
        else:
            tgt = coordinator.current_targets.get(drone_id)
            if tgt is None:
                nom_gN, nom_gE = global_N, global_E   # hover in place
            else:
                nom_gN, nom_gE = tgt[0], tgt[1]

        nom_down = -SHOW_ALT_M

        # 6. APF offset from all other drones' current global positions
        others = [
            (coordinator.current_positions[j][0], coordinator.current_positions[j][1])
            for j in range(N_DRONES)
            if j != drone_id and j in coordinator.current_positions
        ]
        apf_dN, apf_dE = compute_apf_offset((global_N, global_E), others, drone_id)

        # 7. Final setpoint in local NED
        final_gN = nom_gN + apf_dN
        final_gE = nom_gE + apf_dE
        final_lN, final_lE = _global_to_local(final_gN, final_gE, drone_id)

        await drone.offboard.set_position_ned(
            PositionNedYaw(final_lN, final_lE, nom_down, 0.0)
        )

        # 8. Sleep for remainder of tick
        elapsed_tick = time.monotonic() - tick_start
        sleep_time = max(0.0, CONTROL_DT - elapsed_tick)
        await asyncio.sleep(sleep_time)

    # ── Land ──────────────────────────────────────────────────────────────────
    print(f"{tag} Show complete — stopping offboard and landing")
    try:
        await drone.offboard.stop()
    except Exception:
        pass
    await drone.action.land()

    async for armed in drone.telemetry.armed():
        if not armed:
            print(f"{tag} Disarmed.")
            break
