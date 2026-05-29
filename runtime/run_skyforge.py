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
os.environ.setdefault("MAVSDK_CALLBACK_DEBUGGING", "0")

import asyncio
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
_HERE        = os.path.dirname(os.path.abspath(__file__))
_SKYFORGE_DIR = os.path.abspath(os.path.join(_HERE, ".."))   # skyforge/ (this file lives in skyforge/runtime/)
if _SKYFORGE_DIR not in sys.path:
    sys.path.insert(0, _SKYFORGE_DIR)

# LED control subprocess needs GZ_IP to reach the Gazebo transport server.
os.environ.setdefault("GZ_IP", "127.0.0.1")

import mavsdk as _mavsdk_mod
from mavsdk import System

from core.show_format.reader import from_json, from_msgpack
from show.config import MIN_SEP_M, APF_MIN_SEP_M
from show.skyforge_adapter import (
    SkyforgeRuntime, run_drone_skyforge, telemetry_consumer,
)

_MAVLINK_BASE      = 15000
_GRPC_BASE         = 50051
_MAVSDK_SERVER_BIN = os.path.join(
    os.path.dirname(_mavsdk_mod.__file__), "bin", "mavsdk_server"
)

DEFAULT_SHOW = os.path.join(_SKYFORGE_DIR, "shows/four_drone_demo.skyforge.json")


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


async def main(show_path: str, allow_unvalidated: bool = False):
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

    # ── Safety gate: refuse to fly a show that wasn't validated at compile time ─
    status = show.metadata.validation_status
    if status != "validated":
        if allow_unvalidated:
            print(f"[run_skyforge] WARNING: validation_status={status!r} — flying anyway "
                  f"(--allow-unvalidated).")
        else:
            print(f"ERROR: show validation_status={status!r}, expected 'validated'. "
                  f"Re-compile with `skyforge compile`, or pass --allow-unvalidated to override.")
            return

    # ── Contract check: does the runtime enforce what the show was planned for? ─
    cms = show.metadata.compile_min_sep_m
    if cms <= 0.0:
        print("[run_skyforge] WARNING: show predates compile-contract metadata; "
              "cannot verify its separation assumptions.")
    elif abs(cms - MIN_SEP_M) > 1e-6:
        print(f"[run_skyforge] WARNING: show planned for min_sep={cms:.2f} m but runtime "
              f"enforces MIN_SEP_M={MIN_SEP_M:.2f} m (APF emergency at {APF_MIN_SEP_M:.2f} m) — "
              f"separation guarantees may not hold.")
    if show.metadata.deconflicted and not show.metadata.deconflict_resolved:
        print("[run_skyforge] WARNING: show compiled with UNRESOLVED trajectory conflicts — "
              "collisions are possible; online APF is the only remaining safeguard.")

    n       = show.metadata.n_drones
    runtime = SkyforgeRuntime(show)

    # Ports are derived from the show's drone count, not from config.py,
    # so any size show works without setting N_DRONES in the environment.
    grpc_ports    = [_GRPC_BASE    + i for i in range(n)]
    mavlink_ports = [_MAVLINK_BASE + i for i in range(n)]

    # ── GCS beacon ────────────────────────────────────────────────────────────
    # PX4's GCS link hard-codes remote=14550 for ALL instances; without something
    # listening there PX4 denies arm ("No connection to GCS"). One beacon receives
    # heartbeats from every instance and replies, satisfying the check fleet-wide.
    # (run_commander.py spawns the same beacon — the show player previously only
    # worked if a beacon was left running from a prior commander session.)
    print("[run_skyforge] Spawning GCS beacon on port 14550...")
    await asyncio.create_subprocess_exec(
        _MAVSDK_SERVER_BIN, "-p", "50050", "udpin://0.0.0.0:14550",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(0.5)

    # ── Pre-spawn one MAVSDK server per drone (staggered) ─────────────────────
    print(f"[run_skyforge] Spawning {n} MAVSDK servers (staggered)...")
    async def _spawn(i: int) -> None:
        await asyncio.sleep(i * 0.2)   # let the OS bind each UDP/gRPC port in turn
        await asyncio.create_subprocess_exec(
            _MAVSDK_SERVER_BIN,
            "-p", str(grpc_ports[i]),
            f"udpin://0.0.0.0:{mavlink_ports[i]}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    await asyncio.gather(*[_spawn(i) for i in range(n)])
    await asyncio.sleep(3.0)   # final settle — last server needs its MAVLink handshake

    # ── Open gRPC channels; tolerate per-drone failure, respawn & retry ───────
    async def _connect(i: int) -> System:
        await asyncio.sleep(i * 0.15)
        for attempt in range(3):
            drone = System(mavsdk_server_address="localhost", port=grpc_ports[i])
            print(f"[run_skyforge] Connecting drone {i} on udpin://0.0.0.0:{mavlink_ports[i]} ...")
            await drone.connect()
            try:
                await wait_healthy(drone, i)
                try:
                    await drone.telemetry.set_rate_position_velocity_ned(10.0)
                except Exception:
                    pass
                return drone
            except Exception as e:
                if attempt < 2:
                    print(f"[run_skyforge] Drone {i}: crash (attempt {attempt+1}), respawning...")
                    kill = await asyncio.create_subprocess_exec(
                        "pkill", "-9", "-f", f"mavsdk_server -p {grpc_ports[i]}",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                    await kill.wait()
                    await asyncio.sleep(1.5)
                    await asyncio.create_subprocess_exec(
                        _MAVSDK_SERVER_BIN,
                        "-p", str(grpc_ports[i]),
                        f"udpin://0.0.0.0:{mavlink_ports[i]}",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.sleep(3.0)
                else:
                    raise
        raise RuntimeError(f"Drone {i} unreachable")

    print(f"[run_skyforge] Opening gRPC channels to {n} servers...")
    raw = await asyncio.gather(*[_connect(i) for i in range(n)], return_exceptions=True)
    # Keep (original_id, drone) pairs so trajectory/LED/envelope indexing by
    # drone_id stays correct even when some drones fail to come up.
    active = [(i, r) for i, r in enumerate(raw) if not isinstance(r, Exception)]
    for i, r in enumerate(raw):
        if isinstance(r, Exception):
            print(f"[run_skyforge] Drone {i}: FAILED to connect ({r}), skipping")
    if not active:
        print("[run_skyforge] No drones connected — aborting.")
        return
    runtime.ready_target = len(active)   # sync target = drones actually flying
    print(f"\n[run_skyforge] {len(active)}/{n} drones connected. Starting in 2 s...\n")
    await asyncio.sleep(2.0)

    # ── Shared synchronisation state ─────────────────────────────────────────
    show_start_event = asyncio.Event()
    abort_event      = asyncio.Event()
    show_start_time  = [None]   # [float] — set by last-ready drone
    ready_count      = [0]      # [int]   — incremented as each drone enters offboard

    # ── Telemetry consumers (cache fillers) ───────────────────────────────────
    # One per drone, in its own task, using a plain async-for stream (never
    # wait_for'd). The per-drone control loops READ ONLY the cache these fill.
    telem_tasks = [
        asyncio.create_task(telemetry_consumer(drone, i, runtime, abort_event))
        for i, drone in active
    ]

    # ── Run drone coroutines concurrently ─────────────────────────────────────
    # return_exceptions=True: one drone failure doesn't cascade-cancel the rest.
    try:
        results = await asyncio.gather(*[
            run_drone_skyforge(
                i, drone, runtime,
                show_start_event, abort_event, show_start_time, ready_count,
            )
            for i, drone in active
        ], return_exceptions=True)
    finally:
        # Show over (or errored) — stop telemetry consumers. Cancelling a task
        # parked in `async for` is safe here: the streams are being torn down,
        # not reused.
        abort_event.set()
        for t in telem_tasks:
            t.cancel()
        await asyncio.gather(*telem_tasks, return_exceptions=True)

    for (i, _), r in zip(active, results):
        if isinstance(r, Exception):
            print(f"[run_skyforge] Drone {i} exited with exception: {r}")

    print("\n[run_skyforge] Show finished.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="run_skyforge")
    ap.add_argument("show", nargs="?", default=DEFAULT_SHOW,
                    help="path to a .skyforge or .skyforge.json show file")
    ap.add_argument("--allow-unvalidated", action="store_true",
                    help="fly even if the show's validation_status != 'validated' (UNSAFE)")
    a = ap.parse_args()
    path = os.path.abspath(a.show)
    if not os.path.exists(path):
        print(f"ERROR: Show file not found: {path}")
        sys.exit(1)
    try:
        asyncio.run(main(path, allow_unvalidated=a.allow_unvalidated))
    except KeyboardInterrupt:
        print("\n[run_skyforge] Interrupted.")
        sys.exit(0)
