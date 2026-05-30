# Deploying Skyforge on real PX4 hardware

Skyforge's runtime drives PX4 entirely through **standard MAVSDK** (`action.arm/takeoff/land`,
`offboard.set_position_ned`, `telemetry.*`). That interface is **identical** on SITL, HITL, and a
real Pixhawk — so the choreography engine itself is hardware-agnostic. This document covers the
**config-driven retarget** that lets the same `run_commander.py` / `run_skyforge.py` talk to real
flight controllers instead of local Gazebo SITL instances.

## What this enables — and what it does NOT

**Enables (software, verified by unit tests):**
- Point each drone's `mavsdk_server` at a **real MAVLink endpoint** (`serial://…`, `udp://host:port`)
  via a JSON fleet file — no code change.
- Disable the **SITL-only GCS beacon** (real PX4 supplies its own GCS heartbeat).
- Swap the **LED backend** from Gazebo to a stub (or a real driver you add).

**Does NOT (deferred — see "Gaps" below):**
- No hardware LED driver (only Gazebo + a no-op stub ship).
- No geodetic origin / lat-lon-alt — the runtime is **pure local NED**.
- No safety layer (geofence / RTL / kill-switch / battery & RC-loss failsafes). Configure those
  **on the PX4 vehicle**.

## How the connection works (only one string changes)

```
PX4 (SITL instance OR real Pixhawk)
  └─ MAVLink ──► mavsdk_server   (runs on the show-control computer)
        └─ gRPC localhost:{grpc_port} ──► Python System
```

A local `mavsdk_server` per drone always runs on the control computer. The **only** thing that
varies per deployment is the MAVLink URL it connects to:

| Deployment | `mavlink_url` |
|---|---|
| SITL (default) | `udpin://0.0.0.0:{15000+i}` (a local Gazebo PX4 instance) |
| Telemetry radio / USB | `serial:///dev/ttyUSB0:57600` |
| Networked (WiFi/Ethernet bridge) | `udp://192.168.1.51:14550` |

The gRPC side stays `localhost:{50051+i}`. (You *can* run `mavsdk_server` on a companion board and
point Python at it with a per-drone `grpc_host` — see the schema.)

## Fleet file (`SKYFORGE_FLEET`)

Set `SKYFORGE_FLEET=/path/to/fleet.json` before launching. With it **unset**, behavior is the exact
historical SITL configuration (byte-for-byte).

```jsonc
{
  "spawn_local_server": true,        // default true; false = a mavsdk_server is already running
  "use_gcs_beacon":     false,       // default true (SITL); false on real PX4
  "grpc_host":          "localhost", // default; per-drone override allowed
  "grpc_base":          50051,       // default (matches show/config.py GRPC_BASE)
  "drones": [
    { "mavlink_url": "serial:///dev/ttyUSB0:57600" },
    { "mavlink_url": "udp://192.168.1.51:14550", "grpc_port": 50052 },
    { "mavlink_url": "serial:///dev/ttyACM0:921600", "grpc_host": "10.0.0.7", "grpc_port": 50100 }
  ]
}
```

- `drones[i].mavlink_url` — **required** per drone; passed verbatim to `mavsdk_server`.
- `drones[i].grpc_port` / `grpc_host` — optional overrides (default `grpc_base+i` / fleet `grpc_host`).
- **Drone count:** if `drones` is present, its length is the fleet size. `run_commander` adopts it;
  `run_skyforge` **fails loud** if the file lists *fewer* drones than the compiled show needs (it
  will not fly a choreographed show on fewer airframes).

> ⚠️ **Remote `grpc_host` requires `spawn_local_server: false`.** This code only ever spawns
> `mavsdk_server` *locally*. If you point a drone at a remote `grpc_host` (a companion board) you
> **must** set `spawn_local_server: false` and pre-start `mavsdk_server` on that host — otherwise the
> local machine spawns a server while the `System` connects to the remote host (mismatch). The
> runtime **warns** at startup if it detects a remote `grpc_host` with `spawn_local_server: true`.
> Also: when `spawn_local_server: false`, a *dead* remote server can't be restarted from here — the
> retry loop only waits for it to reappear, so monitor/restart it externally.

**Flags-only file** (no `drones`) keeps SITL ports/URLs and just flips flags — handy for HITL:

```json
{ "use_gcs_beacon": false }
```

## LED backend (`SKYFORGE_LED_BACKEND`)

| Value | Effect |
|---|---|
| unset / `gazebo` | SITL: player recolors emissive meshes, commander recolors arm-tip lights (default) |
| `stub` | No-op (no `gz` subprocesses). Use on hardware until a real driver is wired. |

**Adding a real LED driver:** implement `set_led(drone_id, r, g, b)` in a new `LedBackend` subclass
in `runtime/show/led_backend.py` (e.g. MAVLink, DroneCAN, or companion-computer GPIO/serial) and
return it from `make_led_backend()`. The two call sites (player show loop, commander `led_watcher`)
already delegate to the backend — nothing else changes.

## Bring-up checklist

1. Flash PX4 to each board; confirm each is reachable (`mavlink` over the chosen link).
2. Write a fleet file; `export SKYFORGE_FLEET=…` and `export SKYFORGE_LED_BACKEND=stub`.
3. **Single drone first:** a 1-drone fleet file → `run_commander.py 1` → confirm the server
   connects, `_wait_healthy` passes (GPS + home), then `takeoff` / `land`.
4. Configure PX4-side **failsafes** (geofence, RTL on RC/GCS loss, low-battery action) in
   QGroundControl — Skyforge does **not** provide these.
5. Scale up the fleet file; verify each drone reaches `Ready for takeoff!` before flying a show.

## GCS beacon

The beacon on UDP 14550 exists **only** because PX4 **SITL** hard-codes `remote=14550` and denies
arm without a GCS heartbeat there. Real PX4 + a real GCS (QGroundControl) supplies its own — set
`"use_gcs_beacon": false`.

## Verified in software vs. needs hardware

| Verified now (unit tests, no hardware) | Needs hardware to confirm |
|---|---|
| Default profile == exact SITL (byte-for-byte) | Real MAVLink link bring-up (serial/UDP) |
| Fleet-file URL / gRPC / host overrides | Radio/link latency under the 10 Hz offboard loop |
| `use_gcs_beacon` / `spawn_local_server` flags | Arm/takeoff/land on a real airframe |
| LED backend selection + `StubLed` no-op | A real LED driver (none ships) |
| Malformed-fleet-file → `ValueError` | RTK accuracy vs. the 1.5 m separation margin |

## Gaps to close before a real multi-drone show (out of scope here)

- **Geodetic origin.** The compiler and runtime work in **local NED** relative to each drone's
  home. A real outdoor show needs all drones on a **common datum** (survey/RTK) and per-drone home
  reconciliation. `core/show_format/schema.py` has an unused `VenueOrigin` placeholder for this.
- **Positioning accuracy.** Collision margins are ~1.5 m; consumer GPS (±2–5 m) is **not** safe —
  use **RTK** for cm-level positioning.
- **Safety layer.** Geofence, return-to-launch, kill-switch, and battery/RC-loss failsafes belong on
  the PX4 vehicle. Skyforge's `abort()`/`land()` are *commanded landings*, not a hard emergency stop.

See `docs/HITL.md` for the single-board hardware-in-the-loop validation step.
