#!/usr/bin/env python3
"""
Interactive drone commander.

Connects to N PX4 SITL drones, starts the live REPL, and plays
back commands in real time. Type 'takeoff' to arm, then any
formation command.

Usage:
    python3 run_commander.py [N_DRONES]   # default: N_DRONES env or 10
"""
import asyncio
import math
import os
import sys

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("MAVSDK_CALLBACK_DEBUGGING", "0")
os.environ.setdefault("GZ_IP", "127.0.0.1")

_HERE         = os.path.dirname(os.path.abspath(__file__))
_SKYFORGE_DIR = os.path.abspath(os.path.join(_HERE, ".."))   # skyforge/ (this file lives in skyforge/runtime/)
if _SKYFORGE_DIR not in sys.path:
    sys.path.insert(0, _SKYFORGE_DIR)

import mavsdk as _mavsdk_mod
from mavsdk import System

from commander.dynamic_adapter import (
    DynamicRuntime, run_drone_commander, led_watcher, telemetry_consumer,
)
from commander.commander import FleetCommander
from commander.cli import cli_loop
from show.config import (
    MAVLINK_BASE as _MAVLINK_BASE, GRPC_BASE as _GRPC_BASE,
    GCS_BEACON_MAVLINK, GCS_BEACON_GRPC,
)
_MAVSDK_SERVER_BIN = os.path.join(
    os.path.dirname(_mavsdk_mod.__file__), "bin", "mavsdk_server"
)


async def _wait_healthy(drone: System, drone_id: int, timeout: float = 60.0) -> None:
    """Wait until PX4 EKF has global position + home — guarantees arm() won't be denied.
    gRPC errors propagate to _connect for retry. Timeout → proceed anyway."""
    async def _wait():
        async for health in drone.telemetry.health():
            if health.is_global_position_ok and health.is_home_position_ok:
                return
    try:
        await asyncio.wait_for(_wait(), timeout=timeout)
        print(f"[run_commander] Drone {drone_id}: ready (GPS + home OK)")
    except asyncio.TimeoutError:
        print(f"[run_commander] Drone {drone_id}: WARNING — EKF not ready after {timeout:.0f}s, proceeding")
    # gRPC exceptions propagate to _connect so it can respawn and retry


async def main(n: int) -> None:
    print("=" * 55)
    print("  Drone Commander — Live Interactive Mode")
    print(f"  Fleet: {n} drones")
    print("=" * 55)

    cols          = math.ceil(math.sqrt(n))
    home_ned_list = [(2.0 * (i // cols), 2.0 * (i % cols)) for i in range(n)]

    runtime     = DynamicRuntime(n_drones=n, home_ned_list=home_ned_list)
    abort_event = asyncio.Event()

    # ── Pre-spawn all mavsdk_server processes concurrently (truly async) ──────
    # Bypasses MAVSDK's internal blocking subprocess.Popen so all 25 start
    # simultaneously instead of one-by-one (saves ~20-40 s on large fleets).
    # Pre-spawn each server WITH its connection URL so it connects to PX4
    # immediately — Python then just opens the gRPC channel (fast, non-blocking).
    # Stagger spawn 200 ms apart so OS can bind each UDP socket + gRPC port
    # before the next one opens.  25 drones ≈ 5 s total spawn time, then we
    # wait another 3 s for the last servers to complete their MAVLink handshake.
    # GCS beacon: PX4's "Normal" GCS link hard-codes remote=14550 for ALL instances.
    # Without something listening there, PX4 refuses to arm ("No connection to GCS").
    # One server on 14550 receives heartbeats from every PX4 instance and replies,
    # satisfying the GCS-connected check for the whole fleet.
    print("[run_commander] Spawning GCS beacon on port 14550...")
    await asyncio.create_subprocess_exec(
        _MAVSDK_SERVER_BIN, "-p", str(GCS_BEACON_GRPC), f"udpin://0.0.0.0:{GCS_BEACON_MAVLINK}",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(0.5)

    print(f"[run_commander] Spawning {n} MAVSDK servers (staggered)...")
    async def _spawn(i: int) -> None:
        await asyncio.sleep(i * 0.2)
        await asyncio.create_subprocess_exec(
            _MAVSDK_SERVER_BIN,
            "-p", str(_GRPC_BASE + i),
            f"udpin://0.0.0.0:{_MAVLINK_BASE + i}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        print(f"[run_commander] Server {i} spawned (grpc={_GRPC_BASE+i}  udp={_MAVLINK_BASE+i})")
    await asyncio.gather(*[_spawn(i) for i in range(n)])
    await asyncio.sleep(3.0)   # final settle — last server needs its MAVLink handshake

    # ── Open gRPC channels to already-connected servers ────────────────────────
    # If a server crashed (gRPC error), kill it, respawn, and retry once.
    async def _connect(i: int) -> System:
        await asyncio.sleep(i * 0.15)   # 150 ms stagger — avoids gRPC subscription burst
        for attempt in range(3):
            drone = System(mavsdk_server_address="localhost", port=_GRPC_BASE + i)
            await drone.connect()
            try:
                await _wait_healthy(drone, i)
                try:
                    await drone.telemetry.set_rate_position_velocity_ned(10.0)
                except Exception:
                    pass
                return drone
            except Exception as e:
                if attempt < 2:
                    print(f"[run_commander] Drone {i}: crash (attempt {attempt+1}), respawning...")
                    kill = await asyncio.create_subprocess_exec(
                        "pkill", "-9", "-f", f"mavsdk_server -p {_GRPC_BASE + i}",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                    await kill.wait()
                    await asyncio.sleep(1.5)
                    await asyncio.create_subprocess_exec(
                        _MAVSDK_SERVER_BIN,
                        "-p", str(_GRPC_BASE + i),
                        f"udpin://0.0.0.0:{_MAVLINK_BASE + i}",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.sleep(3.0)
                else:
                    raise
        raise RuntimeError(f"Drone {i} unreachable")

    print(f"[run_commander] Opening gRPC channels to {n} servers...")
    raw = await asyncio.gather(*[_connect(i) for i in range(n)], return_exceptions=True)
    # Keep (original_id, drone) pairs so formation slots stay correct even if some fail
    active = [(i, r) for i, r in enumerate(raw) if not isinstance(r, Exception)]
    for i, r in enumerate(raw):
        if isinstance(r, Exception):
            print(f"[run_commander] Drone {i}: FAILED to connect ({r}), skipping")
    runtime.ready_target = len(active)   # actual target — may be < original n
    print(f"\n[run_commander] {len(active)}/{n} drones connected.\n")

    commander = FleetCommander(runtime)

    # Build labelled coroutines so exception reporting stays correct regardless
    # of how many helper tasks we add. Each drone gets BOTH a control loop and a
    # telemetry_consumer (plain async-for stream → cache; never wait_for'd).
    labelled = [("cli", cli_loop(commander))]
    for orig_i, drone in active:
        labelled.append((f"drone {orig_i}", run_drone_commander(orig_i, drone, runtime, abort_event)))
    for orig_i, drone in active:
        hn, he = runtime.home_ned[orig_i]
        labelled.append((f"telemetry {orig_i}",
                         telemetry_consumer(drone, orig_i, runtime, hn, he, abort_event)))
    labelled.append(("led_watcher", led_watcher(runtime, abort_event)))

    results = await asyncio.gather(*(c for _, c in labelled), return_exceptions=True)

    for (label, _), r in zip(labelled, results):
        if isinstance(r, Exception):
            print(f"[run_commander] {label} exited with exception: {r}")

    print("\n[run_commander] Done.")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("N_DRONES", "10"))
    try:
        asyncio.run(main(n))
    except KeyboardInterrupt:
        print("\n[run_commander] Interrupted.")
        sys.exit(0)
