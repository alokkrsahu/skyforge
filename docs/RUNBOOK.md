# Skyforge — Operator Runbook

A start‑to‑finish checklist for running a show, wiring together the safety, scale, and ops
features. SITL today; the same flow applies to HITL/hardware once those are validated
([HARDWARE.md](HARDWARE.md), [HITL.md](HITL.md)). For *what's not yet built*, see
[ROADMAP.md](ROADMAP.md); for *how it's tested*, [TESTING.md](TESTING.md).

## 1. Author & compile
```bash
skyforge compile shows/my_show.py --tracking-margin 0.3   # margin = expected real tracking error
skyforge info     shows/my_show.skyforge.json
```
- Designed art (Blender) → a point cloud → `compiler/formations/patterns/<name>.csv`
  (3 columns `dN,dE,dU` = volumetric); see `compiler/formations/patterns/README.md`.
- Compile **only writes if validation passes**; `validation_status` must read `validated`.

## 2. Pre‑flight go / no‑go (dry run, no flight)
```bash
skyforge preflight shows/my_show.skyforge.json --endurance 600 --tracking-margin 0.3
skyforge energy    shows/my_show.skyforge.json --endurance 600     # battery detail
```
**GO** requires: validation PASS **and** battery within budget. Review the geodetic‑origin line
(outdoor ⇒ RTK required).

## 3. Configure the autonomous safety net (per vehicle)
On real PX4 (and confirmed in HITL): provision failsafes from one place —
```bash
export SKYFORGE_FAILSAFE_CONFIG=runtime/failsafe.example.json   # geofence/RTL/battery/RC-loss/offboard-loss
```
These fire **without the ground link** — the load‑bearing safety. The commands below are
*supervisory*, not a substitute.

## 4. Bring up the stack (SITL)
```bash
./t1_sitl.sh 16 default          # PX4 SITL ×16 + Gazebo (DART arena scales to 100+)
./t7_qgc.sh                      # optional monitor;  then run with SKYFORGE_GCS=qgc
```

## 5. Fly
**Small fleet — live commander** (interactive):
```bash
SKYFORGE_BLACKBOX=/tmp/flight.jsonl SKYFORGE_AUTOABORT=1 ./t6_commander.sh 16
#  takeoff → circle / cat / text HELLO → move … → land
```
**Synchronized / at scale — upload‑and‑go agents:**
```bash
./t8_agents.sh shows/my_show.skyforge.json 16     # N agents, one per PX4 instance, shared T0
# (each flies its own `skyforge export` slice autonomously; GCS only sets start/abort)
```

## 6. In‑flight control & emergencies (commander)
| Command | Effect |
|---|---|
| `hold` / `hover` | freeze the whole fleet in place |
| `rtl [s]` | return the fleet to launch, then land (coordinated) |
| `land [now]` | staggered descent (`now` = immediate) |
| `estop` / `abort` | immediate land, no stagger |

Automatic backstops while flying: **dropout policy** (`SKYFORGE_FAIL_MODE=continue|abort`) and,
with `SKYFORGE_AUTOABORT=1`, a **policy auto‑abort** on excess loss / low battery / tracking error.

## 7. Post‑flight
```bash
skyforge flightlog /tmp/flight.jsonl     # worst loss / tracking error / lowest battery over the run
```
Archive the black‑box JSONL + the compiled `.skyforge.json` for the record.

## Emergency quick‑reference
1. **Something wrong, recover:** `hold` → assess → `rtl`.
2. **Get them down now:** `land now`, or `estop`/`abort` (drops in place — only over clear ground).
3. **Link/agent lost:** PX4 failsafes + the broadcast link‑loss ladder (hold → land) act autonomously.

## Known limitations (carry to the brief)
Geodetic origin/RTK, real RF transport, on‑board failsafe param confirmation, battery/GPS telemetry
in the dashboard, and a real LED bus driver are **DEFERRED to hardware** — see [ROADMAP.md](ROADMAP.md).
Outdoor/at‑scale real flight additionally needs RTK and the PX4‑side failsafes configured.
