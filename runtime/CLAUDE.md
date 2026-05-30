# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the System

Three terminals are required. Always start in order:

```bash
# Terminal 1 — PX4 SITL + Gazebo physics server (headless)
./t1_sitl.sh [N]          # N drones, default 4; logs: /tmp/px4_sitl_0.log .. _N-1.log

# Terminal 2 — Gazebo GUI (3D visualization)
./t2_gazebo_gui.sh

# Terminal 3 — choose one mode:
./t6_commander.sh [N]     # interactive REPL (live commands)
./t5_skyforge.sh [file]   # pre-programmed polynomial show
```

Direct Python invocations (same virtualenv at `~/src/PX4-Autopilot/.venv`):
```bash
source ~/src/PX4-Autopilot/.venv/bin/activate
python3 run_commander.py 8
python3 run_skyforge.py ../shows/four_drone_demo.skyforge.json
```

Kill everything between runs:
```bash
pkill -f "run_commander"; pkill -9 -f "mavsdk_server"; sleep 2
```

## Architecture

### Two Execution Modes

**Interactive commander** (`run_commander.py` + `commander/`):
- `FleetCommander` (commander.py) is the high-level API: `takeoff`, `land`, `formation`, `move`, `set_altitude`, `set_color`, `status`, `abort`
- `DynamicRuntime` (dynamic_adapter.py) holds live fleet state: formation targets, transitions, LED color, beat detector
- `run_drone_commander()` is the per-drone asyncio coroutine — loops forever supporting multiple flight cycles via `flight_cycle` counter
- `cli_loop()` (cli.py) reads stdin via `loop.run_in_executor` so it doesn't block
- `formation` is collision-aware: it scales the formation to the fleet
  (`get_formation(spec, n, min_spacing_m=3.0)`) and `assign_nocross`es drones from their
  current positions to slots so a live pattern change (e.g. `circle` → `star`) doesn't fly
  drones through each other. The assignment targets ~2.5 m clearance (margin above the
  1.5 m floor for PX4 tracking error); APF is the reactive backstop. Single-altitude,
  horizontal-only — altitude layering stays offline-only (`t5` player), where transitions
  are long enough for the vertical reconverge to be PX4-feasible.

**Skyforge show** (`run_skyforge.py` + `show/skyforge_adapter.py`):
- Loads `.skyforge.json` (piecewise polynomial trajectories) via `../core/show_format`
- Refuses to fly unless `validation_status == "validated"` (`--allow-unvalidated` to override)
- Drones sync on `show_start_event` then evaluate polynomials at each 10 Hz tick
- No convergence barrier; show time drives everything

> The legacy coordinator-based "traditional show" (`run_show.py`, `t4_show.sh`,
> `show/{coordinator,barrier,bezier,drone_controller,formations}.py`) was **removed** — it was
> broken against the upgraded 3D APF signature and superseded by the two modes above.

### MAVLink / MAVSDK Connection Pipeline

```
PX4 SITL (instance i)
  └─ MAVLink UDP → port 15000+i    (Onboard link, 4 MB/s, set in px4-rc.mavlink)
       └─ mavsdk_server (gRPC port 50051+i)
            └─ Python System(mavsdk_server_address="localhost", port=50051+i)
```

- **GCS beacon**: one extra mavsdk_server on UDP 14550, gRPC 50050. PX4's GCS link hard-codes `remote=14550` for all instances — without something listening there PX4 denies arm with "No connection to GCS".
- Servers are pre-spawned with `asyncio.create_subprocess_exec` (staggered 200 ms) then Python opens gRPC channels separately. This bypasses MAVSDK's internal blocking `subprocess.Popen`.
- `_wait_healthy()` waits for `is_global_position_ok AND is_home_position_ok` (up to 60 s) before proceeding — this ensures the EKF has converged and `arm()` won't be denied.
- **Arm is crash-hardened.** `mavsdk_server` *aborts* (`std::bad_optional_access` in its lazy `Action` plugin) if `arm()` reaches a momentarily-disconnected system — which kills the server and surfaces as "Connection reset by peer", taking down the fleet at takeoff under load (low RTF widens the PX4→server heartbeat gap). So `run_drone_commander` re-confirms readiness with `_ensure_ready()` (a *fresh* `telemetry.health()` check) immediately before every arm, and if a server dies mid-arm it `_respawn_server()`s that drone and retries — the System's gRPC channel and its `telemetry_consumer` auto-reconnect to the fresh server on the same ports. The old code just "skipped the cycle", leaving the server dead so every later takeoff failed too. Tested in `tests/unit/test_commander_arm.py`.

### Port Assignments

| Purpose | Port |
|---------|------|
| MAVLink per drone (Onboard) | 15000 + i |
| gRPC per drone | 50051 + i |
| GCS beacon UDP | 14550 |
| GCS beacon gRPC | 50050 |

The port mapping `15000+i` is set in **two places** that must stay in sync:
1. `~/src/PX4-Autopilot/build/px4_sitl_default/etc/init.d-posix/px4-rc.mavlink` (the live file PX4 reads — NOT the ROMFS source)
2. `MAVLINK_BASE` in `show/config.py` — the single source of truth imported by both `run_commander.py`
   and `run_skyforge.py` (also `GRPC_BASE`, `GCS_BEACON_MAVLINK`/`GCS_BEACON_GRPC`).

If you change ports, update both. The build file is a plain copy, not a symlink — edit it directly.

### Targeting real PX4 (HITL / hardware)

Both run scripts resolve a **deployment profile** via `show/connection.py` (`load_profile`). With no
env set, it's the exact SITL config above. To target HITL or real hardware, set:

- **`SKYFORGE_FLEET`** = path to a JSON fleet file giving each drone a real `mavlink_url`
  (`serial:///dev/ttyUSB0:57600`, `udp://host:14550`) and flags (`use_gcs_beacon:false` on real PX4,
  `spawn_local_server:false` if a `mavsdk_server` is already running). Only the MAVLink endpoint
  string changes — the gRPC side stays local.
- **`SKYFORGE_LED_BACKEND`** = `gazebo` (default) or `stub` (no-op; for headless HITL/hardware until a
  real LED driver is added behind `show/led_backend.py`).

Full schema, bring-up checklist, and the deferred gaps (geodetic origin, safety/failsafe) are in
`docs/HARDWARE.md` and `docs/HITL.md`.

### Collision Avoidance (APF)

`show/apf.py` — 3D velocity-aware repulsion:
- Horizontal influence radius: 4.0 m, gain 0.8, max offset 2.5 m
- Vertical influence radius: 3.0 m, gain 0.4, max offset 1.5 m  
- Emergency max repulsion if any neighbour < 1.2 m (3D distance)
- Repulsion only fires when drones are **approaching** (closing speed > 0), preventing jitter on separation
- `drone_id * 0.01` asymmetric perturbation breaks symmetric deadlocks

### LED / Visual Updates

LEDs are set via Gazebo's `visual_config` **service** (not `light_config` topic — that silently ignores model-embedded lights):
```bash
GZ_IP=127.0.0.1 gz service -s /world/default/visual_config \
  --reqtype gz.msgs.Visual --reptype gz.msgs.Boolean --timeout 200 \
  --req 'name: "x500_0::base_link::5010_motor_base_0" material {emissive {r:1 g:0 b:0 a:1}}'
```
Targets 4 motor-base visuals per drone (`5010_motor_base_0..3`). Emissive material is always visible.

### Multi-flight Cycle

After `land`, drone coroutines loop back and wait for `runtime.flight_cycle > last_cycle`. Typing `takeoff` again increments `flight_cycle` and resets `ready_count`/`transition`. Crucially it **rises each drone IN PLACE** — `hold_pos` keeps the current (landed) XY and only the altitude is set. (It used to reset `hold_pos` to the home grid, so a takeoff after a formation+land converged the whole spread fleet onto the tight 2 m home grid at once → pile-up → PX4 "Attitude failure (roll)" tumble.) Rearrange after takeoff with a `formation` command, which does the planned crossing-free transition. The session stays alive indefinitely.

### Gazebo Physics

World SDF: `~/src/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf`
- Uses **DART** physics (not ODE) at 100 Hz — avoids ODE integer overflow crash at 42+ drones
- Rotor collision geometries removed from `x500_base/model.sdf` (reduced broadphase pairs)

### Arena / World ("forest clearing")

The installed `default.sdf` is a **forest-clearing stage** generated by
`runtime/worlds/make_forest_stage.py` (repo copy: `runtime/worlds/forest_stage.sdf`).
PX4 picks the world from `PX4_GZ_WORLD` (unset → `default`), so this is the world every
mode flies in.

- **Still named `<world name="default">`** on purpose — keeps DART@100 Hz *and* every
  hardcoded `/world/default/…` path working untouched: `t1` model-remove, `t2` GUI
  readiness poll (`/world/default/clock`), and the LED `visual_config`/`light_config`
  services. (Changing the *inner* world name, e.g. by setting `PX4_GZ_WORLD=walls`,
  would break all five — that's why we swap scenery instead.)
- **Trees are VISUAL-ONLY** (no `<collision>`), so they're invisible to the headless
  physics server (`t1`) and cannot hang SITL, stall `/clock`, or crash a drone — they
  render only in the `t2` GUI. PX4's stock `forest.sdf` is *not* usable as-is: its trees
  are full-mesh ~8 m collision obstacles sitting inside the takeoff grid, and it uses
  ODE@250 Hz (crashes at 42+ drones).
- **Backdrop ring** of 16 oak/pine at radius 30–46 m around a grass clearing; nearest
  tree is ~31 m out, ~6 m clear of the largest show footprint (`N=100` formations reach
  ~24 m). Caveat: a pathological giant single formation (e.g. a 100-drone circle at 3 m
  spacing ≈ 48 m radius) would visually overlap the tree line — harmless (no collision),
  just looks odd.
- **Shadows are OFF** (scene + sun `cast_shadows=false`): shadow passes over the tree
  meshes were the heaviest GUI cost and dragged the physics-server RTF down on this
  single-machine setup. Tree count was also halved (34→16) for the same reason. Re-enable
  in `make_forest_stage.py` if running headless or on a beefier GPU.
- **Fuel assets** (oak/pine/grasspatch) are pre-downloaded into `~/.gz/fuel` so nothing
  downloads at launch. If the cache is wiped, re-fetch with
  `gz fuel download -u "https://fuel.gazebosim.org/1.0/OpenRobotics/models/Oak Tree" -t model`
  (likewise `Pine Tree` and `hexarotor/models/grasspatch`).
- **Revert to the plain gray world:**
  `cp ~/src/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf.plain.bak \
      ~/src/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf`
- **Regenerate / tweak the layout:** edit `make_forest_stage.py` (ring radii, tree count,
  clearing size), then `python3 make_forest_stage.py out.sdf && cp out.sdf <worlds>/default.sdf`.

## Key Configuration (`show/config.py`)

```python
CONTROL_HZ = 10          # setpoint rate
SHOW_ALT_M = 5.0         # default cruise altitude
APF_D0 = 4.0             # APF horizontal influence radius
APF_MIN_SEP_M = 1.2      # emergency hold threshold
```

## Skyforge Dependency

This runtime lives **inside** the skyforge repo at `skyforge/runtime/`. The skyforge
package root (`..`) is added to `sys.path` at runtime. Key imports:
- `from compiler.formations import get_formation` — formation geometry (circle, star, text:X, …)
- `from core.show_format.reader import from_json` — load `.skyforge.json` show files
- `from show.skyforge_adapter import SkyforgeRuntime` — polynomial evaluator

Formation spec strings: `"circle"`, `"star"`, `"text:HELLO"`, `"circle:radius_m=8"`, `"grid:spacing=4"`, single capital letter as shorthand for `"text:X"`.
