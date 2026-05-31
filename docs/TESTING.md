# Testing Skyforge

Two layers: **automated** (hermetic — no PX4/Gazebo/hardware) and **manual** (you run the SITL
stack / hardware). The automated suite is the regression gate; the manual procedures cover what
can't be unit-tested (real Gazebo rendering, PX4 arming, the load-dependent arm race, hardware).

## Automated (run anytime)

```bash
source ~/src/PX4-Autopilot/.venv/bin/activate
pytest -q                       # full suite (compiler + runtime + deployment)
bash -n runtime/t1_sitl.sh runtime/t2_gazebo_gui.sh runtime/t5_skyforge.sh runtime/t6_commander.sh
```

What it covers: the connection profile (`load_profile`, flags, overrides, fleet-size helpers,
remote-host warning), the LED backend factory + Gazebo path + stub, the gz-world resolver
(env/fallback/cache/regex), the commander arm crash-hardening (`_ensure_ready` + respawn-retry),
telemetry-consumer recovery, and the run-script **connect phase** (`_connect_fleet`) — beacon
spawned iff `use_gcs_beacon`, N local spawns iff `spawn_local_server`, `System` built from
`conn.grpc_host`/`grpc_port` — all with a stubbed MAVSDK + recorded (not spawned) subprocesses.

Script help/validation (no stack, no teardown):
```bash
./t1_sitl.sh -h            # lists arenas + caveats, exit 0
./t1_sitl.sh 4 nosuchworld # errors + lists + exit 1
```

## Manual — SITL (you run the 3-terminal stack)

### 1. Regression (default arena, no env) — must behave exactly as before
```bash
./t1_sitl.sh 4      # T1
./t2_gazebo_gui.sh  # T2
./t6_commander.sh 4 # T3 → takeoff ; set_color red ; circle ; land
```
Expect: forest world renders; GCS beacon spawned; LEDs recolor; kill a PX4 instance and confirm the
restart helper logs `/world/default/remove`. This is the byte-for-byte-default check.

### 2. Arena matrix
```bash
./t1_sitl.sh 4 walls        # log shows "world: walls"; t2 attaches; LEDs recolor; restart remove works
./t1_sitl.sh 4 windy        # wind world
./t1_sitl.sh 4 frictionless # inner-name MISMATCH (filename frictionless, world name "default") —
                            #   confirms auto-detect uses the inner name; LEDs + remove still work
./t1_sitl.sh 4 forest       # stock forest — expect the Fuel-download note + collision-tree caveat
```
For each: `t6` → `set_color`/`takeoff` recolors LEDs (proves the resolver built `/world/<inner>/…`).

### 3. Arm-hardening (opportunistic — load-dependent)
```bash
./t1_sitl.sh 16 ; ./t2_gazebo_gui.sh ; ./t6_commander.sh 16 → takeoff
```
Watch for `link down pre-arm — respawning` or `arm/takeoff failed (attempt N)` **followed by a
successful arm** rather than a dead fleet. The mavsdk_server abort race is load-dependent — it may
not trigger on a given run; that's fine, the unit tests pin the recovery logic.

## Manual — HITL proxy on SITL (verifies the deployment paths without a board)

Create `fleet_proxy.json` (SITL ports, but exercising the flags):
```json
{ "use_gcs_beacon": false,
  "drones": [ { "mavlink_url": "udpin://0.0.0.0:15000" },
              { "mavlink_url": "udpin://0.0.0.0:15001" } ] }
```
```bash
export SKYFORGE_FLEET=$PWD/fleet_proxy.json
export SKYFORGE_LED_BACKEND=stub
./t1_sitl.sh 2 ; ./t6_commander.sh 2
```
Expect: **"GCS beacon disabled"** printed; `[led] …=stub — LED commands are no-ops`; **no** `gz
service` procs (`pgrep -f 'gz service'` empty). With `use_gcs_beacon:false`, PX4 SITL will **deny
arm** ("No connection to GCS") — *this proves the flag is honored*. To fly the full path in SITL,
either drop the flag (beacon on) or start one manually:
`mavsdk_server -p 50050 udpin://0.0.0.0:14550 &`.

`spawn_local_server:false` variant: pre-start the per-drone servers yourself
(`mavsdk_server -p 50051 udpin://0.0.0.0:15000 &`, etc.), set the flag, and confirm the runtime
connects **without** spawning new servers (process count unchanged).

## Manual — monitoring with QGroundControl (optional, any mode)

QGC and Skyforge use **different** PX4 links: QGC owns the GCS link (UDP **14550**, where every SITL
instance sends its GCS stream — disambiguated by `MAV_SYS_ID = instance+1`); Skyforge owns the
**onboard** links (`15000+i`). So they don't conflict — but QGC and Skyforge's *beacon* both want
14550, so in QGC mode the beacon steps aside (`SKYFORGE_GCS=qgc`) and QGC supplies the GCS heartbeat
PX4's arm gate wants (exactly like real hardware).

```bash
./t1_sitl.sh 2                       # T1
./t7_qgc.sh                          # opens QGroundControl (auto-connects 14550)
SKYFORGE_GCS=qgc ./t6_commander.sh 2 # T3 — runs the show; Skyforge skips its beacon
```
Expect: QGC **shows both drones** with live telemetry; the runtime prints **"GCS beacon disabled"**;
drones **arm/takeoff** (this confirms QGC's heartbeat satisfies the arm gate). **QGC must be open
before you arm** in `qgc` mode (it is the GCS now). Default (`SKYFORGE_GCS` unset) keeps the headless
beacon and behaves exactly as before. Same `SKYFORGE_GCS=qgc` knob applies to the HITL-proxy and real
hardware. *(If arm is denied even with QGC up, that's the documented fallback: keep the beacon and
attach QGC via a MAVLink router.)*

## Manual — roadmap features (SITL)

Live checks for features built per `docs/ROADMAP.md` (automated logic is pinned by the unit suite;
these confirm behaviour on a running fleet).

**Fleet emergency commands (ROADMAP #4).** `./t1_sitl.sh 4` → `./t6_commander.sh 4` → `takeoff` → `circle`, then:
- `hold` (or `hover`) mid-transition → **Pass:** the fleet freezes in place immediately.
- `rtl` → **Pass:** all drones fly back over their home XY at cruise altitude, then land (staggered).
- `land now` → **Pass:** immediate descent (no per-drone stagger). `land` alone staggers.
- `estop` (alias of `abort`) → **Pass:** immediate land, no stagger.

**Mid-show resilience (ROADMAP #8).** Airborne fleet, then kill one drone's link
(`pkill -f "mavsdk_server.*5005<i>"` or stop one PX4 instance):
- default (`SKYFORGE_FAIL_MODE=continue`) → **Pass:** `[monitor] Lost drones [i] … continuing`; the
  survivors keep flying the formation (the lost drone drops out of APF/sync, no ghost chase).
- `SKYFORGE_FAIL_MODE=abort ./t6_commander.sh N` → **Pass:** the same loss triggers a fleet emergency land.

## Manual — real hardware (deferred; needs a board)

Follow `docs/HITL.md` then `docs/HARDWARE.md`. Order: real MAVLink link (`serial://`/`udp://`) →
single-drone `_wait_healthy` (real GPS+home) → arm/takeoff/land → scale up. Remember:
- **Remote `grpc_host` ⇒ `spawn_local_server: false`** + pre-start `mavsdk_server` on that host.
- **Geodetic origin is unimplemented** — real *outdoor multi-drone* needs a common datum (RTK) +
  per-drone home reconciliation; indoor/known-origin swarms are unaffected (see HARDWARE.md "Gaps").
- Configure PX4-side failsafes (geofence/RTL/battery/RC-loss) on the vehicle — Skyforge doesn't.
