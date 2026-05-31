"""
Upload-and-go ON-BOARD AGENT (SITL proof-of-concept) — ROADMAP #1 core.

Each drone flies its OWN validated trajectory slice autonomously; the ground station
only sets a shared start instant (``SKYFORGE_T0_EPOCH``) and an abort. This is the
control model real 1000-drone shows use — the per-host 10 Hz setpoint stream in
``run_skyforge.py`` doesn't scale past ~dozens. For SITL we run N copies of this agent,
one per PX4 instance (``t8_agents.sh``); on hardware it runs on the drone's companion.

It deliberately REUSES the player: load the 1-drone slice (`skyforge export`) into a
``SkyforgeRuntime``, connect THIS drone's PX4 instance, and fly via ``run_drone_skyforge``,
which already honours ``SKYFORGE_T0_EPOCH`` for a synchronized start. A lone agent has no
neighbour telemetry, so there is no inter-drone APF — the offline plan is collision-free by
construction; trusting it (with RTK accuracy) is the upload-and-go contract.

DEFERRED (hardware): real companion-computer deployment, a broadcast start/abort channel
(here: shared epoch + local abort), and RF. The pure control law below is unit-tested; the
live flight path is exercised in SITL (see docs/TESTING.md).
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys

# repo root + runtime on the path (script-run and import both work)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.show_format.reader import from_json, from_msgpack
from core.show_format.schema import ShowFile


def load_slice(path: str) -> ShowFile:
    """Load a per-drone trajectory slice (a 1-drone ShowFile from `skyforge export`)."""
    show = from_json(path) if path.endswith(".json") else from_msgpack(path)
    if show.metadata.n_drones != 1:
        raise ValueError(
            f"on-board agent expects a 1-drone slice (got n_drones={show.metadata.n_drones}); "
            f"produce one with `skyforge export <show> --drone N`")
    return show


class OnboardAgent:
    """The on-board control law: evaluate MY validated trajectory (and LED) at show_time.
    Pure — no MAVSDK — so it is unit-testable and identical to the player's nominal path."""

    def __init__(self, slice_show: ShowFile):
        if slice_show.metadata.n_drones != 1:
            raise ValueError("OnboardAgent needs a 1-drone slice")
        self.show     = slice_show
        self.traj     = slice_show.trajectories[0]
        self.led      = slice_show.led_tracks[0] if slice_show.led_tracks else None
        self.duration = slice_show.metadata.duration_s

    def position_at(self, show_time: float) -> tuple[float, float, float]:
        v = self.traj.evaluate(show_time)
        return (v.n, v.e, v.d)

    def color_at(self, show_time: float) -> tuple[float, float, float]:
        if self.led is None:
            return (0.0, 0.0, 0.0)
        c = self.led.evaluate(show_time)
        return (c.r, c.g, c.b)


def agent_conn(drone_id: int):
    """The connection for THIS agent's PX4 instance, indexed as drone 0 in its 1-drone
    runtime (so it targets MAVLink/gRPC ports for instance ``drone_id`` while the show
    has a single trajectory at id 0)."""
    from show.connection import sitl_default_conn
    return dataclasses.replace(sitl_default_conn(drone_id), drone_id=0)


async def run_agent(drone_id: int, slice_path: str) -> None:
    """Connect this drone's PX4 instance and fly its slice (T0-synced). Live path."""
    import asyncio
    from show import config
    from show.connection import FleetProfile
    from show.skyforge_adapter import SkyforgeRuntime, run_drone_skyforge, telemetry_consumer
    import run_skyforge as player                       # reuse spawn+connect+respawn

    show    = load_slice(slice_path)
    runtime = SkyforgeRuntime(show)
    profile = FleetProfile(
        conns=(agent_conn(drone_id),), spawn_local_server=True, use_gcs_beacon=False,
        gcs_beacon_mavlink=config.GCS_BEACON_MAVLINK, gcs_beacon_grpc=config.GCS_BEACON_GRPC,
    )
    active = await player._connect_fleet(1, profile, runtime)
    if not active:
        print(f"[agent {drone_id}] no PX4 connection — aborting"); return

    _, drone = active[0]
    show_start_event = asyncio.Event(); abort_event = asyncio.Event()
    show_start_time  = [None];          ready_count = [0]
    telem = asyncio.create_task(telemetry_consumer(drone, 0, runtime, abort_event))
    print(f"[agent {drone_id}] flying slice {os.path.basename(slice_path)} "
          f"(T0 = $SKYFORGE_T0_EPOCH or on-ready)")
    try:
        await run_drone_skyforge(0, drone, runtime,
                                 show_start_event, abort_event, show_start_time, ready_count)
    finally:
        abort_event.set(); telem.cancel()


def main() -> None:
    import asyncio
    ap = argparse.ArgumentParser(prog="onboard_agent",
                                 description="Upload-and-go: fly one trajectory slice autonomously")
    ap.add_argument("--drone-id", type=int, required=True, help="PX4 instance index for this drone")
    ap.add_argument("--trajectory", required=True, help="Per-drone slice (.skyforge.json from `export`)")
    args = ap.parse_args()
    asyncio.run(run_agent(args.drone_id, args.trajectory))


if __name__ == "__main__":
    main()
