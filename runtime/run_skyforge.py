#!/usr/bin/env python3
"""
Skyforge show runner.

Loads a .skyforge or .skyforge.json show file and plays it via MAVSDK + PX4 SITL.
Run after t1_sitl.sh and t2_gazebo_gui.sh are up.

Usage:
    python3 run_skyforge.py [/path/to/show.skyforge.json]

Default show: ../shows/four_drone_demo.skyforge.json
"""
import os
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

import asyncio
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))   # skyforge/runtime/
_SKYFORGE_DIR = os.path.abspath(os.path.join(_HERE, ".."))   # skyforge/
for _p in (_SKYFORGE_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# LED control subprocess needs GZ_IP to reach the Gazebo transport server.
os.environ.setdefault("GZ_IP", "127.0.0.1")

from mavsdk import System

from core.show_format.reader import from_json, from_msgpack
from show.skyforge_adapter import SkyforgeRuntime, run_drone_skyforge

_MAVLINK_BASE = 14540
_GRPC_BASE    = 50051

DEFAULT_SHOW = os.path.join(_SKYFORGE_DIR, "shows", "four_drone_demo.skyforge.json")


def load_show(path: str):
    if path.endswith(".json"):
        return from_json(path)
    return from_msgpack(path)


async def wait_healthy(drone: System, drone_id: int, timeout: float = 60.0):
    print(f"[run_skyforge] Drone {drone_id}: waiting for GPS + home position...")
    async def _wait():
        async for health in drone.telemetry.health():
            if health.is_global_position_ok and health.is_home_position_ok:
                return
    try:
        await asyncio.wait_for(_wait(), timeout=timeout)
        print(f"[run_skyforge] Drone {drone_id}: healthy")
    except asyncio.TimeoutError:
        print(f"[run_skyforge] Drone {drone_id}: WARNING — health timeout after {timeout:.0f}s, proceeding anyway")


async def main(show_path: str):
    print("=" * 55)
    print("  Skyforge Runtime")
    print(f"  Show: {os.path.basename(show_path)}")
    print("=" * 55)

    show = load_show(show_path)
    print(
        f"[run_skyforge] Loaded: '{show.metadata.name}'  "
        f"{show.metadata.n_drones} drones  {show.metadata.duration_s:.0f}s  "
        f"{sum(len(t.segments) for t in show.trajectories)} segments"
    )

    n       = show.metadata.n_drones
    runtime = SkyforgeRuntime(show)

    # Ports are derived from the show's drone count, not from config.py,
    # so any size show works without setting N_DRONES in the environment.
    grpc_ports    = [_GRPC_BASE    + i for i in range(n)]
    mavlink_ports = [_MAVLINK_BASE + i for i in range(n)]

    # ── Sequential connect ────────────────────────────────────────────────────
    drones = []
    for i in range(n):
        drone = System(port=grpc_ports[i])
        print(f"[run_skyforge] Connecting drone {i} on udpin://:{mavlink_ports[i]} ...")
        await drone.connect(system_address=f"udpin://:{mavlink_ports[i]}")
        await wait_healthy(drone, i)
        drones.append(drone)

    print(f"\n[run_skyforge] All {n} drones connected. Starting in 2 s...\n")
    await asyncio.sleep(2.0)

    # ── Shared synchronisation state ─────────────────────────────────────────
    show_start_event = asyncio.Event()
    abort_event      = asyncio.Event()
    show_start_time  = [None]   # [float] — set by last-ready drone
    ready_count      = [0]      # [int]   — incremented as each drone enters offboard

    # ── Run all drone coroutines concurrently ─────────────────────────────────
    await asyncio.gather(*[
        run_drone_skyforge(
            i, drones[i], runtime,
            show_start_event, abort_event, show_start_time, ready_count,
        )
        for i in range(n)
    ])

    print("\n[run_skyforge] Show finished.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SHOW
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f"ERROR: Show file not found: {path}")
        sys.exit(1)
    try:
        asyncio.run(main(path))
    except KeyboardInterrupt:
        print("\n[run_skyforge] Interrupted.")
        sys.exit(0)
