#!/usr/bin/env python3
"""
Drone show entry point.
Connects all 4 drones sequentially (avoids MAVSDK subprocess-spawn blocking),
then runs the coordinator + 4 drone controllers concurrently.

Run after t1_sitl.sh and t2_gazebo_gui.sh.
"""
import asyncio
import sys

from mavsdk import System

from show.barrier import ShowBarrier
from show.config import GRPC_PORTS, MAVLINK_PORTS, N_DRONES
from show.coordinator import ShowCoordinator
from show.drone_controller import run_drone


async def wait_healthy(drone: System, drone_id: int):
    print(f"[run_show] Drone {drone_id}: waiting for GPS health...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            break
    print(f"[run_show] Drone {drone_id}: ready")


async def main():
    print("=" * 50)
    print(" Drone Show System")
    print("=" * 50)

    # ── Sequential connect (avoids asyncio event-loop blocking) ──────────────
    drones = []
    for i in range(N_DRONES):
        drone = System(port=GRPC_PORTS[i])
        print(f"[run_show] Connecting drone {i} on udp://:{MAVLINK_PORTS[i]} ...")
        await drone.connect(system_address=f"udp://:{MAVLINK_PORTS[i]}")
        await wait_healthy(drone, i)
        drones.append(drone)

    print(f"\n[run_show] All {N_DRONES} drones connected. Starting show...\n")

    # ── Shared objects ────────────────────────────────────────────────────────
    barrier     = ShowBarrier()
    coordinator = ShowCoordinator(barrier)

    # ── Run coordinator + all drone controllers concurrently ─────────────────
    await asyncio.gather(
        coordinator.coordinator_loop(),
        *[run_drone(i, drones[i], coordinator) for i in range(N_DRONES)],
    )

    print("\n[run_show] Show finished.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[run_show] Interrupted.")
        sys.exit(0)
