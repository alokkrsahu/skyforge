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
    monitor_fleet_health,
)
from commander.commander import FleetCommander
from commander.cli import cli_loop
from show.connection import load_profile, FleetProfile, reconcile_commander_fleet_size
from show.failsafe_provisioning import FailsafeConfig, provision_failsafes
_MAVSDK_SERVER_BIN = os.path.join(
    os.path.dirname(_mavsdk_mod.__file__), "bin", "mavsdk_server"
)


async def _spawn_server(i: int, profile: FleetProfile) -> None:
    """Start one mavsdk_server for drone i, connected to its MAVLink endpoint
    (SITL: udpin://0.0.0.0:{port}; hardware: serial:// or udp://host:port).
    No-op when the profile says the servers are owned elsewhere (HITL/hardware)."""
    if not profile.spawn_local_server:
        return
    conn = profile.conn(i)
    await asyncio.create_subprocess_exec(
        _MAVSDK_SERVER_BIN, "-p", str(conn.grpc_port), conn.mavlink_url,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )


async def _respawn_server(i: int, profile: FleetProfile) -> None:
    """Kill drone i's (crashed) mavsdk_server and start a fresh one on the SAME
    gRPC port + MAVLink endpoint. The drone's existing System channel and its
    telemetry_consumer auto-reconnect to the new server, so callers keep their
    System handle. Used to recover from the mavsdk_server abort on arm
    (bad_optional_access in the lazy Action plugin when the link has dropped
    under load). When the profile doesn't own the servers, just wait for the
    existing System's gRPC channel to auto-reconnect."""
    if not profile.spawn_local_server:
        print(f"[run_commander] Drone {i}: server externally managed — waiting for gRPC "
              f"reconnect (if it's down, restart mavsdk_server on its host).")
        await asyncio.sleep(3.0)
        return
    kill = await asyncio.create_subprocess_exec(
        "pkill", "-9", "-f", f"mavsdk_server -p {profile.conn(i).grpc_port}",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await kill.wait()
    await asyncio.sleep(1.0)
    await _spawn_server(i, profile)
    await asyncio.sleep(3.0)   # let the new server complete its MAVLink handshake


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


async def _connect_fleet(n: int, profile: FleetProfile, runtime: DynamicRuntime):
    """Spawn the GCS beacon (if enabled) + one mavsdk_server per drone (if owned
    locally), open gRPC channels with respawn-retry, and return the list of
    (original_id, System) that came up. Extracted from main() so the beacon/spawn/
    connect wiring is integration-testable with a stubbed System + recorded spawns."""
    # GCS beacon: PX4 SITL hard-codes remote=14550 for ALL instances and refuses to
    # arm ("No connection to GCS") without something listening there. Real PX4
    # supplies its own GCS heartbeat, so a fleet file can disable this.
    if profile.use_gcs_beacon:
        print(f"[run_commander] Spawning GCS beacon on port {profile.gcs_beacon_mavlink}...")
        await asyncio.create_subprocess_exec(
            _MAVSDK_SERVER_BIN, "-p", str(profile.gcs_beacon_grpc),
            f"udpin://0.0.0.0:{profile.gcs_beacon_mavlink}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(0.5)
    else:
        print("[run_commander] GCS beacon disabled (real PX4 supplies its own GCS heartbeat).")

    print(f"[run_commander] Spawning {n} MAVSDK servers (staggered)...")
    async def _spawn(i: int) -> None:
        await asyncio.sleep(i * 0.2)
        await _spawn_server(i, profile)
        conn = profile.conn(i)
        print(f"[run_commander] Server {i} spawned (grpc={conn.grpc_port}  link={conn.mavlink_url})")
    await asyncio.gather(*[_spawn(i) for i in range(n)])
    await asyncio.sleep(3.0)   # final settle — last server needs its MAVLink handshake

    # ── Open gRPC channels; if a server crashed, respawn and retry ──────────────
    async def _connect(i: int) -> System:
        await asyncio.sleep(i * 0.15)   # 150 ms stagger — avoids gRPC subscription burst
        conn = profile.conn(i)
        for attempt in range(3):
            drone = System(mavsdk_server_address=conn.grpc_host, port=conn.grpc_port)
            await drone.connect()
            try:
                await _wait_healthy(drone, i)
                try:
                    await drone.telemetry.set_rate_position_velocity_ned(10.0)
                except Exception:
                    pass
                return drone
            except Exception:
                if attempt < 2:
                    print(f"[run_commander] Drone {i}: crash (attempt {attempt+1}), respawning...")
                    await _respawn_server(i, profile)
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
    return active


async def main(n: int) -> None:
    # Resolve the deployment profile (SITL default, or a $SKYFORGE_FLEET file for
    # HITL/hardware). A fleet file with an explicit drone list is the source of
    # truth for the count, so adopt it.
    profile = load_profile(n)
    for w in profile.warnings:
        print(f"[run_commander] WARNING: {w}")
    n, msg = reconcile_commander_fleet_size(profile, n)
    if msg:
        print(f"[run_commander] {msg}")

    print("=" * 55)
    print("  Drone Commander — Live Interactive Mode")
    print(f"  Fleet: {n} drones")
    print("=" * 55)

    cols          = math.ceil(math.sqrt(n))
    home_ned_list = [(2.0 * (i // cols), 2.0 * (i % cols)) for i in range(n)]

    runtime     = DynamicRuntime(n_drones=n, home_ned_list=home_ned_list)
    abort_event = asyncio.Event()

    active = await _connect_fleet(n, profile, runtime)

    # Opt-in: push PX4 failsafes/geofence to every connected drone before arming.
    # No-op unless $SKYFORGE_FAILSAFE_CONFIG is set (SITL default leaves PX4 as-is).
    fs_cfg = FailsafeConfig.from_env()
    if fs_cfg is not None:
        print("[run_commander] Provisioning PX4 failsafes (SKYFORGE_FAILSAFE_CONFIG)...")
        for orig_i, drone in active:
            applied = await provision_failsafes(drone, fs_cfg)
            print(f"[run_commander] drone {orig_i}: set {len(applied)} failsafe params")

    commander = FleetCommander(runtime)

    # Build labelled coroutines so exception reporting stays correct regardless
    # of how many helper tasks we add. Each drone gets BOTH a control loop and a
    # telemetry_consumer (plain async-for stream → cache; never wait_for'd).
    labelled = [("cli", cli_loop(commander))]
    for orig_i, drone in active:
        labelled.append((f"drone {orig_i}",
                         run_drone_commander(orig_i, drone, runtime, abort_event,
                                             lambda d: _respawn_server(d, profile))))
    for orig_i, drone in active:
        hn, he = runtime.home_ned[orig_i]
        labelled.append((f"telemetry {orig_i}",
                         telemetry_consumer(drone, orig_i, runtime, hn, he, abort_event)))
    labelled.append(("led_watcher", led_watcher(runtime, abort_event)))
    # Opt-in observability: $SKYFORGE_BLACKBOX → JSONL flight recorder;
    # $SKYFORGE_AUTOABORT=1 → automatic fleet abort on a policy breach (battery/loss/error).
    _bb = None
    if os.environ.get("SKYFORGE_BLACKBOX", "").strip():
        from show.fleet_monitor import BlackBox
        _bb = BlackBox(os.environ["SKYFORGE_BLACKBOX"].strip())
    _policy = None
    if os.environ.get("SKYFORGE_AUTOABORT", "").strip() in ("1", "true", "yes"):
        from show.fleet_monitor import AbortPolicy
        _policy = AbortPolicy()
    labelled.append(("health_monitor",
                     monitor_fleet_health(runtime, abort_event, black_box=_bb, abort_policy=_policy)))

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
