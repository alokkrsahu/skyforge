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

### 2. Positioning & geodetic origin — 🔴
**Local NED only — no geodetic origin** (`VenueOrigin` schema field exists but is unused); no common
datum, no per-drone home reconciliation. **No RTK integration** — consumer GPS (±2–5 m) is unsafe
against the 1.5 m collision margin. (Documented in [HARDWARE.md](HARDWARE.md).)

### 3. Time synchronization — 🔴
**No GPS/PPS shared T0** — drones can't all start the same trajectory at the same instant. Without it
the show desyncs and the collision guarantees (which assume one shared clock) no longer hold.

### 4. On-vehicle safety & fleet emergency — 🔴
Skyforge ships **no failsafes** — geofence / RTL / battery / RC-loss / kill all live on PX4 and must be
hand-configured per vehicle (documented gap). **Missing:** auto-geofence generation from the show
envelope, automated failsafe provisioning, and a **fleet-wide emergency** (one "ALL HOLD / ALL LAND /
ALL RTL" broadcast). Today `abort()`/`land()`/`hover()` require a live link and act per drone.

### 5. Comms / link layer — 🔴 / 🟡
**No telemetry-radio / WiFi-mesh / broadcast integration** — assumes localhost/private net. No
fleet-level link-loss handling, no bandwidth budgeting, and **MAVLink/gRPC are unencrypted**
(acceptable in a lab, not over a hostile/public RF environment).

### 6. Bulk provisioning & fleet management — 🔴
**No batch firmware/param flashing**, no automated **`MAV_SYS_ID`** assignment, no
**id ↔ takeoff-slot ↔ trajectory** manifest tooling, no **per-drone trajectory upload** mechanism
(the `SKYFORGE_FLEET` file is addressing for the *live* model, not bulk upload), no spare/hot-swap
workflow, and no fleet **pre-flight go/no-go** health gate.

### 7. Monitoring & observability at scale — 🟡
No **fleet dashboard** (aggregate battery / GPS fix / position-error / armed state), no **black-box
logging** for post-flight/compliance, no **anomaly detection → auto-abort** triggers. QGC monitors a
handful, not thousands.

### 8. Mid-show resilience — 🟡
**No dynamic failure handling** — if a drone drops out, neighbours don't adapt and the figure isn't
re-assigned; no graceful visual degradation. The plan is static once compiled.

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
