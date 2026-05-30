"""
Deployment / connection profile — lets the SAME runtime target SITL, HITL, or real
PX4 hardware purely by configuration.

The runtime drives PX4 through MAVSDK, which behaves identically on SITL, HITL, and
real hardware. The only things that vary per deployment are (a) the MAVLink endpoint
string each local ``mavsdk_server`` connects to — the gRPC side stays local — and
(b) whether the SITL-only GCS beacon is needed.

With no ``SKYFORGE_FLEET`` env var set, :func:`load_profile` returns the exact
historical SITL configuration (one ``mavsdk_server`` per drone on
``udpin://0.0.0.0:{MAVLINK_BASE+i}``, ``System`` on ``localhost:{GRPC_BASE+i}``, GCS
beacon on 14550) — byte-for-byte unchanged.

Fleet file (JSON, path in ``SKYFORGE_FLEET``)::

    {
      "spawn_local_server": true,        # default true; false = servers owned elsewhere
      "use_gcs_beacon":     true,        # default true (SITL); false on real PX4
      "grpc_host":          "localhost", # default; per-drone override allowed
      "grpc_base":          50051,       # default config.GRPC_BASE
      "drones": [                        # optional; omit for a flags-only file
        {"mavlink_url": "serial:///dev/ttyUSB0:57600"},
        {"mavlink_url": "udp://192.168.1.51:14550", "grpc_port": 50052}
      ]
    }

``mavlink_url`` is passed verbatim to ``mavsdk_server`` as its connection URL: the
SITL form is ``udpin://0.0.0.0:{MAVLINK_BASE+i}``; hardware forms are
``serial:///dev/ttyUSB0:57600`` or ``udp://<host>:<port>``.
"""
import json
import os
from dataclasses import dataclass
from typing import Optional

from show import config

SKYFORGE_FLEET_ENV = "SKYFORGE_FLEET"


@dataclass(frozen=True)
class DroneConn:
    """How to reach one drone: the mavsdk_server's MAVLink endpoint + its gRPC channel."""
    drone_id:    int
    mavlink_url: str
    grpc_host:   str
    grpc_port:   int


_LOCAL_HOSTS = ("localhost", "127.0.0.1")


@dataclass(frozen=True)
class FleetProfile:
    conns:              tuple                # tuple[DroneConn, ...]
    spawn_local_server: bool
    use_gcs_beacon:     bool
    gcs_beacon_mavlink: int
    gcs_beacon_grpc:    int
    warnings:           tuple = ()           # config-sanity warnings for the run scripts to print

    @property
    def n(self) -> int:
        return len(self.conns)

    def conn(self, i: int) -> DroneConn:
        return self.conns[i]


def sitl_default_conn(i: int) -> DroneConn:
    """The historical SITL endpoint for drone i (local Gazebo PX4 instance)."""
    return DroneConn(
        drone_id=i,
        mavlink_url=f"udpin://0.0.0.0:{config.MAVLINK_BASE + i}",
        grpc_host="localhost",
        grpc_port=config.GRPC_BASE + i,
    )


def _sitl_profile(n: int) -> FleetProfile:
    return FleetProfile(
        conns=tuple(sitl_default_conn(i) for i in range(n)),
        spawn_local_server=True,
        use_gcs_beacon=True,
        gcs_beacon_mavlink=config.GCS_BEACON_MAVLINK,
        gcs_beacon_grpc=config.GCS_BEACON_GRPC,
    )


def load_profile(n: int, fleet_path: Optional[str] = None) -> FleetProfile:
    """Resolve the fleet connection profile.

    n           : the caller's drone count (argv / show metadata). Used as the
                  fleet size when the fleet file has no explicit ``drones`` list.
    fleet_path  : explicit path (tests). When None, reads ``$SKYFORGE_FLEET``; if
                  that is also unset/empty, returns the exact SITL default.

    With a ``drones`` list of length m, the returned profile has m conns — the
    CALLER reconciles m against its own n (run_skyforge fails loud if m < n; the
    commander adopts n = m). A flags-only file (no ``drones``) keeps the SITL
    connection defaults and only applies the flags.

    Raises ValueError on a missing/malformed fleet file.
    """
    if fleet_path is None:
        fleet_path = os.environ.get(SKYFORGE_FLEET_ENV) or None
    if fleet_path is None:
        return _sitl_profile(n)

    try:
        with open(fleet_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Could not load SKYFORGE_FLEET file {fleet_path!r}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"SKYFORGE_FLEET file {fleet_path!r} must be a JSON object")

    spawn_local_server = bool(data.get("spawn_local_server", True))
    use_gcs_beacon     = bool(data.get("use_gcs_beacon", True))
    grpc_host_default  = str(data.get("grpc_host", "localhost"))
    grpc_base          = int(data.get("grpc_base", config.GRPC_BASE))

    drones = data.get("drones")
    if drones is None:
        # Flags-only file: keep SITL connection defaults, apply the flags.
        conns = tuple(sitl_default_conn(i) for i in range(n))
    else:
        if not isinstance(drones, list) or not drones:
            raise ValueError(
                f"SKYFORGE_FLEET file {fleet_path!r}: 'drones' must be a non-empty list")
        parsed = []
        for i, d in enumerate(drones):
            if not isinstance(d, dict) or "mavlink_url" not in d:
                raise ValueError(
                    f"SKYFORGE_FLEET file {fleet_path!r}: drones[{i}] needs a 'mavlink_url'")
            parsed.append(DroneConn(
                drone_id=i,
                mavlink_url=str(d["mavlink_url"]),
                grpc_host=str(d.get("grpc_host", grpc_host_default)),
                grpc_port=int(d.get("grpc_port", grpc_base + i)),
            ))
        conns = tuple(parsed)

    # Sanity: a remote grpc_host can only work if the mavsdk_server is already
    # running THERE — this code only ever spawns servers locally. So a remote host
    # with spawn_local_server=True is a misconfig (server spawns on localhost while
    # the System connects to the remote host). Warn; don't block (the user may route).
    warnings = []
    if spawn_local_server:
        remote = sorted({c.grpc_host for c in conns if c.grpc_host not in _LOCAL_HOSTS})
        if remote:
            warnings.append(
                f"grpc_host {', '.join(remote)} is remote but spawn_local_server=true — "
                f"the local mavsdk_server can't reach a remote board. Set "
                f"spawn_local_server:false and pre-start mavsdk_server on that host.")

    return FleetProfile(
        conns=conns,
        spawn_local_server=spawn_local_server,
        use_gcs_beacon=use_gcs_beacon,
        gcs_beacon_mavlink=config.GCS_BEACON_MAVLINK,
        gcs_beacon_grpc=config.GCS_BEACON_GRPC,
        warnings=tuple(warnings),
    )


# ── Fleet-size reconciliation (pure; the run scripts print the message) ──────────

def reconcile_commander_fleet_size(profile: FleetProfile, requested_n: int):
    """Commander: a fleet file's drone list is the source of truth, so adopt its
    count. Returns (effective_n, message_or_None)."""
    if profile.n != requested_n:
        return profile.n, (f"fleet file overrides drone count: {requested_n} → {profile.n}")
    return requested_n, None


def validate_show_fleet_size(profile: FleetProfile, show_n: int):
    """Show player: the SHOW dictates the count, so the fleet must supply at least
    that many drones. Returns (ok, message_or_None): ok=False aborts (too few);
    ok=True with a message warns (more than needed); ok=True/None is an exact match."""
    if profile.n < show_n:
        return False, (f"$SKYFORGE_FLEET lists {profile.n} drones but the show needs "
                       f"{show_n}. Aborting (won't fly a {show_n}-drone show on "
                       f"{profile.n} drones).")
    if profile.n > show_n:
        return True, (f"fleet file lists {profile.n} drones; using the first {show_n} "
                      f"for this {show_n}-drone show.")
    return True, None
