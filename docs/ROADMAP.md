# Skyforge — Production-Readiness Roadmap

**What this is:** an honest gap analysis for taking Skyforge from *validated in SITL/HITL* to a
*real drone show in the sky*, organized into milestones. Companion docs:
[ARCHITECTURE.md](../ARCHITECTURE.md), [HARDWARE.md](HARDWARE.md), [HITL.md](HITL.md),
[TEST_PLAN.md](TEST_PLAN.md).

**Severity legend:** 🔴 **Blocker** (cannot safely fly a real show without it) ·
🟡 **Important** (needed to scale / look good) · 🟢 **Enhancement**.

---

## What already works (don't rebuild this)

The **offline compiler is the hard part, and it's solid**: a show script compiles to validated,
**collision-free, per-drone polynomial trajectories** (`ShowFile.trajectories[i]`) with a stamped
safety contract, scaling to 100+ drones in ~1 s. SITL/HITL validation, the config-driven deployment
profile, QGC integration, the formations plugin system (incl. volumetric 3D), and 166 automated
tests are all in place. The gaps below are almost entirely in the **real-hardware execution, scale,
and operations** layers — not in the choreography math.

| Phase | State |
|---|---|
| Design → validated trajectories (offline) | ✅ Solid |
| SITL / HITL validation | ✅ Working today |
| Small real fleet (≤ ~tens), live link | 🟡 Reachable after the M1 blockers |
| Hundreds–thousands, production show | 🔴 Needs the M3 architecture work |

---

## Gap list (by theme)

### 1. Control architecture at scale — 🔴
The runtime streams **10 Hz offboard setpoints per drone from one host** (one `mavsdk_server` + one
link each). Fine for SITL/HITL/tens of drones; **won't scale** past dozens (host CPU, radio spectrum,
process count). **Missing:** *upload-and-go autonomy* — an **onboard agent** that executes the
compiled `trajectory[i]` from the drone itself, with the ground station only broadcasting
start/pause/abort and monitoring. This is the central architectural gap for hundreds/thousands.
> **Status:** ◑ SITL PoC landed. (1) **Trajectory slice export** — `skyforge export <show>
> (--drone N | --all)` writes a valid 1-drone ShowFile per drone (`core/show_format/writer.py`).
> (2) **On-board agent** — `runtime/agent/onboard_agent.py` flies one slice autonomously on its own
> PX4 instance, T0-synced via `SKYFORGE_T0_EPOCH`; `t8_agents.sh` launches N agents (one per
> instance) — the upload-and-go control model (GCS only sets start/abort + monitors; no per-host
> setpoint stream). Pure control law unit-tested (`tests/unit/test_onboard_agent.py`); reuses the
> player's connect + `run_drone_skyforge`. **DEFERRED (hardware):** real companion-computer
> deployment, a broadcast start/abort channel, and RF.

### 2. Positioning & geodetic origin — 🔴
**Local NED only — no geodetic origin** (`VenueOrigin` schema field exists but is unused); no common
datum, no per-drone home reconciliation. **No RTK integration** — consumer GPS (±2–5 m) is unsafe
against the 1.5 m collision margin. (Documented in [HARDWARE.md](HARDWARE.md).)

### 3. Time synchronization — 🔴
**No GPS/PPS shared T0** — drones can't all start the same trajectory at the same instant. Without it
the show desyncs and the collision guarantees (which assume one shared clock) no longer hold.
> **Status:** ◑ software foundation landed — `runtime/show/time_sync.py` maps an absolute UNIX/GPS
> epoch to the local monotonic clock; the player pins show-start to `SKYFORGE_T0_EPOCH` when set, and
> `start_transition(start_at=…)` schedules a synchronized future move (drones hold at start_pos until
> T0) (`tests/unit/test_time_sync.py`). **DEFERRED (hardware):** a real GPS/PPS source replacing
> `time.time()` + sub-ms drift compensation.

### 4. On-vehicle safety & fleet emergency — 🔴
Skyforge ships **no failsafes** — geofence / RTL / battery / RC-loss / kill all live on PX4 and must be
hand-configured per vehicle (documented gap). **Missing:** auto-geofence generation from the show
envelope, automated failsafe provisioning, and a **fleet-wide emergency** (one "ALL HOLD / ALL LAND /
ALL RTL" broadcast). Today `abort()`/`land()`/`hover()` require a live link and act per drone.
> **Status:** ✅ commander fleet-emergency verbs landed — `hold`/`hover`, `land [now]`, `rtl`
> (return-to-launch then land), `estop`/`abort` (`runtime/commander/{commander,cli}.py`,
> `tests/unit/test_commander_emergency.py`). **Failsafe provisioning** also landed —
> `runtime/show/failsafe_provisioning.py` pushes geofence/RTL/battery/RC-loss/offboard-loss params to
> each drone before arming (opt-in via `$SKYFORGE_FAILSAFE_CONFIG`; `runtime/failsafe.example.json`;
> `tests/unit/test_failsafe_provisioning.py`). Remaining: a true link-independent broadcast (item 13)
> and **DEFERRED (hardware)** on-board param confirmation in HITL.

### 5. Comms / link layer — 🔴 / 🟡
**No telemetry-radio / WiFi-mesh / broadcast integration** — assumes localhost/private net. No
fleet-level link-loss handling, no bandwidth budgeting, and **MAVLink/gRPC are unencrypted**
(acceptable in a lab, not over a hostile/public RF environment).

### 6. Bulk provisioning & fleet management — 🔴
**No batch firmware/param flashing**, no automated **`MAV_SYS_ID`** assignment, no
**id ↔ takeoff-slot ↔ trajectory** manifest tooling, no **per-drone trajectory upload** mechanism
(the `SKYFORGE_FLEET` file is addressing for the *live* model, not bulk upload), no spare/hot-swap
workflow, and no fleet **pre-flight go/no-go** health gate.
> **Status:** ◑ manifest tooling landed — `DroneConn` now carries `sys_id`/`home_ned`/`slot`/
> `trajectory_file`, parsed by `load_profile`; `build_fleet_manifest()` generates the
> id↔`MAV_SYS_ID`(=id+1)↔slot↔trajectory mapping (`runtime/show/connection.py`,
> `tests/unit/test_connection.py`). Remaining: batch firmware/param **flashing** and the upload step
> itself need real boards (**DEFERRED**); per-drone failsafe param push already exists (item 4).

### 7. Monitoring & observability at scale — 🟡
No **fleet dashboard** (aggregate battery / GPS fix / position-error / armed state), no **black-box
logging** for post-flight/compliance, no **anomaly detection → auto-abort** triggers. QGC monitors a
handful, not thousands.
> **Status:** ◑ `runtime/show/fleet_monitor.py` — `summarize()` aggregates per-drone health
> (seen/lost, worst battery, worst tracking error), `should_auto_abort()` decides on a policy breach,
> `BlackBox` is a JSONL flight recorder; wired into the commander's monitor loop (opt-in
> `$SKYFORGE_BLACKBOX`, `$SKYFORGE_AUTOABORT`) (`tests/unit/test_fleet_monitor.py`). DEFERRED: battery/
> GPS telemetry subscription + a real ground dashboard UI.

### 8. Mid-show resilience — 🟡
**No dynamic failure handling** — if a drone drops out, neighbours don't adapt and the figure isn't
re-assigned; no graceful visual degradation. The plan is static once compiled.
> **Status:** ◑ dropout **detection + policy** landed — `monitor_fleet_health` flags a drone whose
> telemetry goes stale and applies `SKYFORGE_FAIL_MODE` (`continue` drops it from APF/sync so the show
> goes on; `abort` lands the fleet) (`runtime/commander/dynamic_adapter.py`, `tests/unit/test_resilience.py`).
> Remaining: dynamic slot **re-assignment** so the figure visually closes the gap.

### 9. Environment / wind / battery — 🟡
**No wind compensation or weather gating**; trajectories assume ideal tracking. No **battery-aware
show duration** / per-drone energy budgeting / charge management. No real tracking-error model fed
back into validation.

### 10. Hardware LED / payload drivers — 🟡
**LED is Gazebo-only**; the hardware path is a **no-op stub** — you must implement a real `LedBackend`
(`runtime/show/led_backend.py`) for your LED hardware. No support for other payloads (pyro, streamers).

### 11. Show-authoring tooling — 🟢 / 🟡
**Blender → formation is ad-hoc** (manual via MCP) — no packaged export add-on/pipeline. No
timeline/choreography editor, limited music-sync authoring (beat detection is basic), no full-show
**3D preview** before flight.

### 12. Validation realism — 🟡
The validator is **offline/static** and deconfliction can still **diverge on very dense fields**
(mitigated by `verified_layering`, not guaranteed). No real-world tracking-error / wind margin in the
model; APF is a reactive backstop, not a primary guarantee.

### 13. Regulatory & operational — 🟢
No flight-log export for compliance, no airspace/NOTAM hooks, no rehearsal/dry-run mode distinct from
the live arming path, no operator console/runbook.

---

## The four true pre-flight blockers

Before *any* real flight, regardless of fleet size:
1. **RTK positioning** (#2) — cm-level fixes against the 1.5 m margin.
2. **Time sync** (#3) — shared GPS/PPS T0.
3. **PX4 failsafes + fleet emergency** (#4) — geofence/RTL/battery/RC-loss/kill + an all-stop.
4. **Hardware LED driver** (#10) — or the show has no visible effect.

The **scale blocker** before a *large* show is **#1 (upload-and-go autonomy)** plus **#6 (bulk
provisioning)**.

---

## Milestones

### M1 — One real drone (bench → first flight)
Goal: a single PX4 vehicle flies a compiled trajectory safely.
- 🔴 Configure + verify PX4 failsafes & geofence (#4); prove link-loss → Hold/Land in HITL.
- 🔴 RTK or mocap positioning for the test area (#2); home reconciliation to the show origin.
- 🔴 Hardware LED driver `LedBackend` (#10) or accept LED-less first flight.
- 🟡 Pre-flight health gate for one drone (GPS fix, battery, EKF) before arm.
- *Exit:* one drone takes off, flies a short validated figure, lands; failsafes demonstrated.

### M2 — Small fleet (≤ ~tens, live link)
Goal: the current live-setpoint runtime drives a real small fleet.
- 🔴 Time sync (#3) so the fleet starts together.
- 🔴 Telemetry-radio / WiFi link integration (#5) + fleet link-loss behaviour.
- 🔴 Fleet-wide emergency: ALL HOLD / ALL LAND / ALL RTL broadcast (#4).
- 🟡 Fleet monitoring dashboard + go/no-go gate (#6, #7).
- 🟡 Battery-aware show duration (#9).
- *Exit:* a synchronized, collision-free multi-drone figure outdoors with a working abort path.

### M3 — Large show (hundreds → thousands)
Goal: production-scale, autonomous.
- 🔴 **Upload-and-go autonomy** (#1): onboard agent executing `trajectory[i]`; GCS = coordinator + monitor.
- 🔴 Bulk provisioning (#6): batch firmware/params, `MAV_SYS_ID` assignment, id↔slot↔trajectory manifest, per-drone trajectory upload, spare swap.
- 🟡 Scaled observability + anomaly→auto-abort (#7); mid-show resilience / re-assignment (#8).
- 🟡 Broadcast/mesh comms + bandwidth budgeting (#5).
- *Exit:* hundreds+ drones launch from uploaded trajectories, GPS/RTK-synced, monitored and abortable as a fleet.

### Continuous (any milestone)
- 🟢 Authoring tooling (#11), validation realism / wind & tracking-error margins (#12), regulatory/ops (#13).

---

## Notes
- This roadmap reflects the system as of the `phase-2-scalability` branch (166 tests; volumetric 3D
  formations; QGC + config-driven HITL). Update it as items land.
- Several blockers (#2, #4) are already called out as gaps in [HARDWARE.md](HARDWARE.md); this doc
  consolidates and prioritizes them with the scale/ops gaps into one place.
