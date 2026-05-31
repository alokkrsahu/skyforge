# Skyforge — Release Test Plan

**Status:** living document · **Baseline:** 166 automated tests green (~1.8 s) · branch `phase-2-scalability`
**Audience:** developers, QA, CI, release managers · **Companion docs:** [TESTING.md](TESTING.md) (manual SITL/HITL detail), [HARDWARE.md](HARDWARE.md), [HITL.md](HITL.md), [../ARCHITECTURE.md](../ARCHITECTURE.md), [../runtime/CLAUDE.md](../runtime/CLAUDE.md)

This plan validates **every** implementation, enhancement, bug fix, refactor, and feature across the
project lifecycle, and establishes that the system is stable, reliable, and ready to fly. It is
**tailored to what Skyforge actually is** — a local PX4 SITL/HITL drone-show platform: an *offline
compiler* (`skyforge` CLI) that turns a show script into validated, collision-free trajectories, and
an *online runtime* (show player + interactive commander) that flies them on PX4 via MAVSDK. There is
no web server, database, user authentication, HTTP API, or multi-tenant surface; categories from a
generic enterprise template that do not apply are listed in §10 with rationale rather than padded.

---

## 1. System overview & what's under test

```
show script (.py) ──skyforge compile──▶ .skyforge[.json]  ──runtime──▶ PX4 SITL/HITL/hardware + Gazebo
  ShowBuilder → trajectories → assign → layer/deconflict → envelopes → validate     (player | commander)
```

| Subsystem | Key modules | What it does |
|---|---|---|
| **Offline compiler** | `compiler/pipeline.py`, `show_builder.py`, `formations/`, `assignment.py`, `deconflict.py`, `verified_layering.py`, `envelope.py`, `validator.py`, `trajectory_generator.py`, `sampling.py` | Acts → per-drone waypoints → cubic-Hermite trajectories; crossing-free assignment; altitude-layered transitions; deconfliction; safety envelopes; validation. Stamps the compile-time safety contract. |
| **Show format** | `core/show_format/schema.py`, `reader.py`, `writer.py` | `ShowFile` (v2 schema) + JSON/msgpack IO; **rejects malformed shows on load** (NaN/inf, length mismatch, bad ids, non-positive duration). |
| **CLI** | `cli.py` | `skyforge compile` / `validate` / `info`; flags `--min-sep`, `--no-validate`; exit codes. |
| **Runtime — player** | `runtime/run_skyforge.py`, `show/skyforge_adapter.py` | Loads a validated show, syncs on `show_start_event`, evaluates polynomials + APF at 10 Hz. **Refuses to fly unless `validation_status=="validated"`.** |
| **Runtime — commander** | `runtime/run_commander.py`, `commander/commander.py`, `commander/cli.py`, `commander/dynamic_adapter.py` | Live REPL: takeoff/land/move/alt/color/formation; crossing-free + **volumetric** transitions; multi-flight cycles; takeoff-in-place. |
| **Connection / deployment** | `runtime/show/connection.py`, `show/config.py` | `load_profile` → SITL by default; `$SKYFORGE_FLEET` JSON for HITL/hardware; GCS modes `beacon`/`qgc`/`none`; per-drone mavsdk_server wiring (MAVLink 15000+i, gRPC 50051+i, GCS 14550). |
| **Collision avoidance (runtime)** | `runtime/show/apf.py` | 3D velocity-aware APF; emergency hold below `APF_MIN_SEP_M`(1.2) < `MIN_SEP_M`(1.5). |
| **LEDs / world** | `runtime/show/led_backend.py`, `gz_world.py`, `drone_lights.py` | Pluggable backend (`gazebo`/`stub`); world auto-detection. |
| **Formations** | `compiler/formations/` (plugin package) | One file per pattern (`patterns/*.py|.csv|.json`); `get_formation` → `(dN,dE,dU)`; volumetric `dU≥0`; robust spacing; the `cat` sculpture. |
| **Launch scripts** | `runtime/t1..t7` | t1 SITL+Gazebo (`[N] [arena]`), t2 GUI, t5 player, t6 commander, t7 QGC. |

---

## 2. Test strategy & layers

| Layer | Scope | Hermetic? | How | Frequency |
|---|---|---|---|---|
| **L0 Static** | Lint/syntax | Yes | `bash -n runtime/t*.sh`; import check | Every commit (CI) |
| **L1 Unit** | Pure logic per module | Yes | `pytest tests/unit` | Every commit (CI) |
| **L2 Integration** | Compiler pipeline, CLI, connection profile, runtime async (MAVSDK **stubbed**) | Yes | `pytest tests/integration` + the runtime/connection unit suites | Every commit (CI) |
| **L3 Manual SITL** | PX4 SITL + Gazebo, real flight in sim | No | [TESTING.md](TESTING.md) §SITL, scripts t1/t2/t5/t6 | Pre-release + on runtime/launch changes |
| **L4 HITL-proxy** | Deployment paths via fleet file on SITL (no board) | No | [TESTING.md](TESTING.md) §HITL-proxy | Pre-release + on connection/profile changes |
| **L5 Hardware** | Single board → fleet | No | [HARDWARE.md](HARDWARE.md), [HITL.md](HITL.md) checklists | Before any real flight |

**Tooling.** pytest 8 (`pyproject.toml` → `testpaths=["tests"]`, `asyncio_mode="auto"`); async bodies are driven with `asyncio.run` (no live event loop needed); MAVSDK and `mavsdk_server` are **stubbed** (fake `System`, monkeypatched `create_subprocess_exec`), `asyncio.sleep` is patched out, env vars are set/restored per test, and the gz-world cache is reset between cases. Deps: numpy, scipy, msgpack (+ pytest, pytest-asyncio).

**Run commands.**
```bash
pytest -q                                  # whole suite (166 tests, ~1.8 s)
pytest -q tests/unit tests/integration     # explicit
pytest -q tests/integration/test_cli.py    # one area
pytest --co -q                             # list/collect (verify traceability IDs exist)
pytest -q --durations=10                   # perf: slowest tests
bash -n runtime/t1_sitl.sh ... t7_qgc.sh   # L0 launch-script syntax
```

**CI recommendation.** GitHub Actions on every push/PR: install `.[dev]`, run `pytest -q` + `bash -n` on all `runtime/t*.sh`. Block merge on failure. (No hardware/Gazebo in CI — L3–L5 are manual gates before a flight/release.)

---

## 3. Test-case specification format

Every L1/L2 case below is identified by its real pytest node id (file::test). Each area table gives
**Objective · Inputs/data · Expected · Pass/Fail · Type**. Prerequisites for all automated cases: repo
checked out, `pip install -e ".[dev]"`. Dependencies are noted only where a case relies on another
component (e.g. CLI compile depends on the pipeline). Manual cases (L3–L5) use the numbered procedures
in §7 with explicit pass/fail.

---

## 4. Per-area automated coverage (L1/L2)

### 4.1 Formations — generators, dispatcher, plugin discovery, robust spacing, volumetric 3D
`tests/unit/test_formations.py` (20), `tests/unit/test_formations_registry.py` (17)
- **Generators**: circle/grid/line/star/v_shape/spiral/text — count, radius, centering, symmetry, text pixel count/scale. Direct calls return **2-tuples** (`test_all_generators_return_2tuples_on_direct_call`).
- **Dispatcher**: `get_formation` for names, `text:STR[:scale]`, custom list, legacy diamond/arrow, unknown→`ValueError`; always returns **3-tuples** (`test_get_formation_returns_3tuples`).
- **Plugin discovery**: catalog lists every shipped pattern; lazy `.py` discovery; CSV/JSON data patterns load + resample (drops/cleans temp files in `patterns/`).
- **Robust spacing**: `min_spacing` scales up; `spacing_percentile` ignores outlier-tight pairs (`test_robust_ignores_outlier_tight_pair`) yet equals default for uniform patterns.
- **Volumetric 3D**: 3-col CSV preserves `dU` (`test_csv_3col_is_volumetric`); 2-col stays flat; **negative dU clamped to 0** (`test_negative_du_is_clamped_to_zero`); `_centre` leaves dU; `_fit_min_spacing` scales all three axes.
- **Pass/Fail**: all assertions hold; **Type**: automated.

### 4.2 Assignment & crossing elimination
`tests/unit/test_assignment_nocross.py` (11), `tests/unit/test_compiler.py` (Hungarian, 5)
- Segment-crossing detection (true/parallel/T-shape); identity/optimal unchanged; head-on & 4-drone crossings swapped out; **collinear same-line** collision caught (the `_segments_cross` blind spot); scaled `text:M→text:U` transition stays ≥ min_sep. **Type**: automated.

### 4.3 Deconfliction & verified layering
`tests/unit/test_deconflict.py` (5), `tests/integration/test_pipeline.py` (deconflict path)
- No-conflict unchanged; head-on & parallel-close resolved ≥ min_sep; correction clamped to `max_deflection`; LED/metadata preserved; crossing show compiles clean. (Convergence/divergence guard exercised via the 100-drone path; dense-field divergence is the documented risk in §6 row F.) **Type**: automated.

### 4.4 Envelopes
`tests/unit/test_envelope.py` (4) — single drone → `max_radius`; coincident → 0; known separation → `(d-min_sep)/2`; demo show all radii ≥ 0. **Type**: automated.

### 4.5 Validator
`tests/unit/test_validator.py` (4) — valid passes; coincident → separation error; unknown reactive primitive → error; temporal gap → error. **Type**: automated.

### 4.6 Schema, reader, writer (data integrity)
`tests/unit/test_schema.py` (6) — Vec3 arithmetic; poly evaluate; LED interpolation; **reader rejects NaN coeff / length mismatch**; JSON round-trip preserves trajectories. **Type**: automated.

### 4.7 CLI (NEW)
`tests/integration/test_cli.py` (9)
- `compile` demo → exit 0, writes `.skyforge.json`+`.skyforge`, status `validated`; missing `builder` → 1; **unknown formation → 1, nothing written**; missing script → 1; `--no-validate` writes without gating; `validate` validated → 0, **too-close show → 1**, missing file → 1; `info` → 0 and prints metadata. **Deps**: compiler pipeline. **Type**: automated.

### 4.8 Connection profile & GCS modes
`tests/unit/test_connection.py` (26)
- Default == **byte-for-byte SITL**; `$SKYFORGE_FLEET` env + file override URLs/gRPC/host/base; flags (`use_gcs_beacon`, `spawn_local_server`); fleet-size reconcile (commander) & validate (player: abort if fleet<show, warn if >); remote-host-with-spawn warning; **missing/malformed file → ValueError**; drone without `mavlink_url` → error; GCS modes `qgc`/`none`/`beacon`, env beats fleet, **unknown mode warns + keeps beacon**. **Type**: automated.

### 4.9 Run-scripts connect phase
`tests/unit/test_run_scripts.py` (6) — `_connect_fleet` (both player & commander): beacon spawned iff `use_gcs_beacon`; local servers spawned iff `spawn_local_server`; System built from `grpc_host/port`. **Type**: automated (MAVSDK/subprocess stubbed).

### 4.10 Commander arm hardening (regression-critical)
`tests/unit/test_commander_arm.py` (5) — `_ensure_ready` true/false (timeout, stream error); **arm failure → respawn server → retry**; skip cycle gracefully without respawn callback. Pins the mavsdk_server SIGABRT fix. **Type**: automated.

### 4.11 Runtime telemetry (regression-critical)
`tests/unit/test_runtime_async.py` (3) — telemetry consumer fills cache with home offset; **re-subscribes after stream ends**; recovers from stream error after backoff. Pins the “never `wait_for` a telemetry generator” rule. **Type**: automated.

### 4.12 APF collision avoidance
`tests/unit/test_apf.py` (14) — zero force when none/receding/out-of-range; repulsion when approaching; **emergency hold below min_sep aggregates all neighbours**, ignores velocity; horizontal+vertical escape together; NE & vertical clamps; symmetry-breaking perturbation. **Type**: automated.

### 4.13 LED backend, gz-world, drone-lights
`tests/unit/test_led_backend.py` (7), `tests/unit/test_gz_world.py` (8), `tests/unit/test_drone_lights.py` (3) — factory default/stub/fallback; stub is true no-op; Gazebo visual/light config calls; `$SKYFORGE_GZ_WORLD` steering; world regex (rejects empty/nested, accepts underscore), `gz`-missing fallback, caching; lazy light-topic resolution. **Type**: automated.

### 4.14 Compiler pipeline (integration)
`tests/integration/test_pipeline.py` (6) — runs demo → validation report; envelopes non-negative; JSON round-trip preserves envelopes; safe show passes & is marked `validated`; crossing show deconflicts clean. **Type**: automated.

### 4.15 Volumetric 3D show + builder altitudes (NEW)
`tests/integration/test_volumetric_show.py` (3), `tests/unit/test_show_builder_3d.py` (4)
- Cat show compiles → `validated`; **min 3D separation ≥ MIN_SEP** over the whole show; **hold altitudes vary** (≥3 levels, spread >3 m) and never below base (`dU≥0`).
- `_append_transition`: flat hold == `-SHOW_ALT_M` (byte-for-byte); volumetric hold == `-SHOW_ALT_M-dU`; flat banded transition keeps original band altitude; **volumetric cross routes above the whole envelope**. **Type**: automated.

---

## 5. Regression suite & previously-fixed defects

The **entire 166-test suite is the regression gate** (must be 100% green before release). Critical
user journeys to re-verify each release: *compile→validate→info* (CLI), *compile→serialize→load→fly*
(player), *connect→takeoff→formation→land→repeat* (commander, manual L3). High-risk/historically
problematic areas get extra attention (manual where not automatable):

| Past defect | Root cause | Pinning test(s) / procedure |
|---|---|---|
| Fleet abandoned at takeoff under load | `mavsdk_server` SIGABRT (`std::bad_optional_access`) on arm | `test_commander_arm.py` (+ L3 arm-hardening §7.3) |
| Telemetry “star doesn't move” / dead stream | `asyncio.wait_for` cancels a telemetry generator | `test_runtime_async.py` |
| Cat formation flew drones ~5× too far | uniform scale to the single tightest pair | `test_formations_registry.py` robust-spacing cases |
| Drone could fly below ground | negative `dU` in a data file | `test_negative_du_is_clamped_to_zero` |
| Direct generator arity drift | v_shape/star/text leaked 3-tuples | `test_all_generators_return_2tuples_on_direct_call` |
| Dense-show deconfliction diverged | straight-line predicate missed bowed-spline conflicts | `verified_layering` + `test_pipeline` (+ L3 100-drone §7.6) |
| Multi-flight crash on re-takeoff | fleet converged to home grid → attitude failure | takeoff-in-place — L3 multi-cycle §7.7 |
| Residual single-drone contact | transitions sat at exactly min_sep | `_PLAN_CROSS_M` margin — `test_assignment_nocross` + L3 |
| Headless SITL hang / ODE crash >42 | camera/shadows & ODE physics | DART default arena — L3 arena matrix §7.2 |

---

## 6. Integration & dependency-failure testing

**Cross-component chains (automated):** compile→`to_json`→`from_json`→`validate` (`test_pipeline`, `test_cli`); `load_profile`→spawn→connect (`test_run_scripts`); formation→assign→trajectory→validate (`test_compiler`, `test_volumetric_show`).

| Dependency failure | Expected behaviour | Coverage |
|---|---|---|
| `mavsdk_server` crash mid-arm | respawn on same ports + retry; else skip cycle (no fleet death) | `test_commander_arm` (auto) + L3 §7.3 |
| Telemetry stream ends / errors | dedicated consumer re-subscribes; control loop reads cache | `test_runtime_async` (auto) |
| `gz` binary missing / world unknown | fallback to `"default"`, never hang (2 s timeout) | `test_gz_world` (auto) |
| Fleet file missing / malformed / no `mavlink_url` | `ValueError` (fail loud, no silent default) | `test_connection` (auto) |
| Partial fleet (some drones never ready) | connect tolerates; fly the drones that came up | `test_run_scripts` (auto) + L3 |
| GCS heartbeat absent | PX4 denies arm (beacon/qgc supplies it) | `test_connection` GCS modes (auto) + L4 §7.4 |
| LED `gz service` storm | fleet-wide semaphore bounds concurrency (no setpoint starvation) | `test_led_backend` (auto) + L3 |

---

## 7. End-to-end manual procedures (L3 SITL / L4 HITL-proxy / L5 hardware)

Full detail in [TESTING.md](TESTING.md); summarized here as release gates with pass/fail. Pre-clean between runs: `pkill -9 -f mavsdk_server; pkill -f run_commander; sleep 2`.

1. **SITL regression baseline** — `./t1_sitl.sh 4` → `./t2_gazebo_gui.sh` → `./t6_commander.sh 4`; `takeoff`/`circle`/`color green`/`land`. **Pass:** all arm, form a circle, LEDs recolor, land; no server deaths.
2. **Arena matrix** — `./t1_sitl.sh 4 <arena>` for `default`(DART), `walls`, `frictionless`, `forest`. **Pass:** world loads, runtime auto-detects it, LEDs recolor. (Stock arenas warn >~40 drones — expected.)
3. **Arm-hardening under load** — `./t1_sitl.sh 16` then `./t6_commander.sh 16`; **Pass:** any `respawning…` lines are followed by successful arm/connect (graceful), final `N/16 connected`. Load-dependent; the logic is pinned by `test_commander_arm`.
4. **HITL-proxy (no board)** — fleet file with `"use_gcs_beacon": false`. **Pass:** PX4 **denies arm** (proves the flag is honored); with `"spawn_local_server": false` and pre-started servers, no new spawns.
5. **QGroundControl** — `./t7_qgc.sh`; `SKYFORGE_GCS=qgc ./t6_commander.sh 4`. **Pass:** QGC shows the vehicles + live telemetry; drones arm (QGC heartbeat satisfies the gate); Skyforge skips its beacon.
6. **100-drone DART** — `./t1_sitl.sh 100 default`; play a compiled 100-drone show. **Pass:** no ODE crash, RTF stays usable, validated show flies without APF-emergency spam.
7. **Volumetric cat live** — `./t1_sitl.sh 31` → `SKYFORGE_GCS=qgc ./t6_commander.sh 31` → `altitude 8` → `cat`. **Pass:** fleet forms a recognizable **3D** cat (varied altitudes), transition completes; `circle` returns it to one plane.
8. **Multi-flight soak** — repeat takeoff→formation→land for ≥5 cycles. **Pass:** every cycle re-arms and re-forms; no drift/attitude failure; stable memory.
9. **Player validation gate** — `./t5_skyforge.sh <validated show>` flies; an `unvalidated` show is **refused** unless `--allow-unvalidated`. **Pass:** gate enforced; `compile_min_sep_m` mismatch warns.
10. **L5 hardware** — follow [HITL.md](HITL.md) then [HARDWARE.md](HARDWARE.md): single board EKF/arm/takeoff/land → formation → scale fleet; configure PX4-side geofence/RTL/battery/RC-loss failsafes first.

---

## 8. Negative, boundary & edge cases

| Input / condition | Expected | Coverage |
|---|---|---|
| Unknown formation name / `info`/`validate` missing file / bad script | `ValueError` / exit 1, no crash, nothing written | `test_formations*`, `test_cli` |
| Malformed CSV/JSON pattern | parsed leniently (2-col→flat) or skipped; never crash | `test_formations_registry` |
| Negative `dU` in data file | clamped to 0 (no below-ground) | `test_negative_du_is_clamped_to_zero` |
| `n=1`, empty pattern | degenerate handled (single point / no scaling) | `test_formations*`, `_fit_min_spacing` guards |
| Fleet < show / fleet > show | abort (loud) / warn | `test_connection` |
| NaN/inf coeffs, length mismatch, dur ≤ 0 | rejected at load | `test_schema` |
| Unknown GCS mode / LED backend / arena | warn + safe fallback (beacon / gazebo / detect) | `test_connection`, `test_led_backend`, L3 §7.2 |
| Stock arena >~40 drones | warn, not blocked (ODE risk) | L3 §7.2 |

---

## 9. Performance (tailored)

| Metric | Target | How |
|---|---|---|
| Full test suite | < 5 s | `pytest -q` (currently ~1.8 s) |
| Compile 4 / 16 / 100 drones | ~1 s @100 (vectorized sampling) | `time skyforge compile shows/hundred_drone_demo.py` |
| Connection bring-up | staggered spawn scales to ≥39 drones | L3 §7.3/§7.6 timing |
| Sim real-time factor (RTF) | usable at target fleet on the host | observe Gazebo/PX4 logs |
| APF per-tick cost | negligible at 10 Hz for target N | profile in soak §7.8 |
| Commander soak | no leak/degradation over ≥5 cycles | L3 §7.8 (watch RSS) |

*N/A:* HTTP throughput, request latency, concurrent-user load — no network service exists.

---

## 10. Security & safety (tailored)

**Applicable:**
- **Validation gate** — player refuses non-`validated` shows (`--allow-unvalidated` is the only override, prints UNSAFE). Reader rejects malformed shows on load. *Coverage:* `test_cli`, `test_schema`, L3 §7.9.
- **Arm gate** — PX4 denies arm without a GCS heartbeat; `beacon`/`qgc` supply it; `use_gcs_beacon:false` proves denial. *Coverage:* `test_connection`, L4 §7.4.
- **Safety-floor invariant** — `APF_MIN_SEP_M (1.2) < MIN_SEP_M (1.5)`; `compile_min_sep_m` mismatch warns at load. *Coverage:* `config.py` constants + L3 §7.9.
- **Secrets/exposure** — the fleet JSON (serial paths, companion-board IPs, ports) is **sensitive**: never commit it; MAVLink/gRPC are **unencrypted** → assume an isolated lab/private network. *Action:* `.gitignore` fleet files; document network assumption ([HARDWARE.md](HARDWARE.md)).
- **Input validation** — only trusted local inputs (show scripts, fleet files, CLI args); reader + CLI handle bad input without corruption.

**N/A (rationale):** web authn/authz, session management, CSRF/XSS, SQL/command injection, third-party API auth, multi-tenant isolation — Skyforge has no web/DB/HTTP/auth surface; it is a single-operator local tool.

---

## 11. Data integrity

JSON ↔ msgpack round-trip preserves trajectories + envelopes (`test_schema`, `test_pipeline`); the
compile-time safety contract (`validation_status`, `compile_min_sep_m`, `deconflict_resolved`, …) is
stamped and re-checked at load; structurally-corrupt shows cannot reach the runtime as “validated”.

---

## 12. Configuration testing

| Knob | Values | Expected | Coverage |
|---|---|---|---|
| `SKYFORGE_FLEET` | unset / path | SITL defaults / parsed profile | `test_connection` |
| `SKYFORGE_GCS` | `beacon`/`qgc`/`none`/blank/unknown | beacon on/off; env beats fleet; unknown→warn+beacon | `test_connection` |
| `SKYFORGE_LED_BACKEND` | `gazebo`/`stub`/unknown | backend select; unknown→gazebo | `test_led_backend` |
| `SKYFORGE_GZ_WORLD` | name/blank | override / ignore + detect | `test_gz_world` |
| `PX4_GZ_WORLD(S)` + arena | t1 `[arena]` | sets world; runtime auto-detects | L3 §7.2 |
| Fleet JSON variants | flags-only / URL / grpc overrides | documented behavior | `test_connection` |
| Defaults | nothing set | byte-for-byte SITL | `test_default_is_exact_sitl` |

---

## 13. Failure-injection

Automated (stubbed): server crash→respawn, telemetry end/error→resubscribe, missing/malformed config→ValueError, partial fleet→tolerate (see §6). Manual (live): kill a `mavsdk_server` mid-flight (L3) → fleet survives; close QGC in `qgc` mode → arm denied next cycle (L4); pull a stock arena at 60 drones → warned, may crash (expected, §7.2).

---

## 14. Traceability matrix (capability/change → tests)

| Capability / change / fix | Automated tests (count) | Manual |
|---|---|---|
| Offline compiler pipeline | `test_pipeline`(6), `test_compiler`(5), `test_trajectory*` | — |
| Assignment / crossing-free | `test_assignment_nocross`(11) | §7.1 |
| Deconfliction / verified layering | `test_deconflict`(5), `test_pipeline` | §7.6 |
| Envelopes | `test_envelope`(4) | — |
| Validator | `test_validator`(4) | §7.9 |
| Schema / reader (data integrity) | `test_schema`(6) | — |
| **CLI compile/validate/info** | `test_cli`(9, NEW) | — |
| Formations plugin + robust spacing | `test_formations`(20), `test_formations_registry`(17) | §7.1 |
| **Volumetric 3D formations** | `test_volumetric_show`(3, NEW), `test_show_builder_3d`(4, NEW), `test_formations_registry` dU cases | §7.7 |
| Connection profile / HITL config | `test_connection`(26) | §7.4 |
| **QGC GCS integration** | `test_connection` GCS modes | §7.5 |
| Run-scripts connect phase | `test_run_scripts`(6) | §7.3 |
| Arm crash-hardening | `test_commander_arm`(5) | §7.3 |
| Telemetry consumer | `test_runtime_async`(3) | — |
| APF (3D) | `test_apf`(14) | §7.6/§7.8 |
| LED backend / world / lights | `test_led_backend`(7), `test_gz_world`(8), `test_drone_lights`(3) | §7.1/§7.2 |
| Arena-agnostic launch | (scripts) `bash -n` | §7.2 |
| Takeoff-in-place / multi-cycle | — | §7.7/§7.8 |
| Player validation gate | `test_cli`, `test_schema` | §7.9 |
| **Total automated** | **166** | 10 procedures |

Verify IDs exist: `pytest --co -q` (every test named above must collect).

---

## 15. Automation vs manual summary

- **Automated (CI, every commit):** all of §4 (166 tests) + L0 `bash -n`. Fast, hermetic, no hardware.
- **Manual (pre-release gates):** §7.1–§7.9 on SITL; §7.4–§7.5 for HITL-proxy/QGC; §7.10 for hardware. Frequency: full L3 before each release and whenever runtime/launch/connection code changes; L5 before any real flight.

---

## 16. Release-readiness checklist & sign-off

**Gate — all must hold before tagging a release:**
- [ ] `pytest -q` = 166/166 green; `pytest --co -q` confirms every traceability ID exists.
- [ ] `bash -n` clean on all `runtime/t*.sh`.
- [ ] Regression table (§5) reviewed; no previously-fixed defect reproduces.
- [ ] L3 SITL §7.1, §7.2 (≥2 arenas), §7.7 (volumetric), §7.9 (gate) executed and **pass**.
- [ ] L3 §7.3 arm-hardening observed at ≥16 drones (graceful recovery).
- [ ] L4 §7.4–§7.5 HITL-proxy + QGC executed and **pass** (if connection/GCS changed).
- [ ] Performance §9 within targets (compile ~1 s @100; suite < 5 s; soak stable).
- [ ] Safety §10: validation gate + arm gate verified; fleet files git-ignored; private-network assumption documented.
- [ ] Known limitations recorded (below) and accepted.

**Known limitations / risk register (carry into release notes):**
- No geodetic origin (local NED only) — outdoor multi-drone needs RTK + home reconciliation. *Risk: HIGH for outdoor; N/A indoor/known-origin.*
- No on-vehicle failsafes in Skyforge — geofence/RTL/kill/battery/RC-loss must be configured on PX4 (QGC Safety). *Risk: HIGH — required before L5.*
- No hardware LED driver (stub ships) — user implements a `LedBackend`. *Risk: LOW (cosmetic).*
- Positioning accuracy — 1.5 m margins assume RTK-class GPS, not consumer GPS. *Risk: HIGH for real flight.*
- Stock (ODE) arenas may crash >~40 drones — use the DART `default` arena at scale. *Risk: MED, mitigated by warning.*
- L3–L5 (live SITL/HITL/hardware) are **manual** — not covered by CI. *Risk: MED — enforce the pre-release gate.*

**Deployment recommendation:** approve for **SITL/HITL development and indoor/known-origin flight**
once the gate above is green. **Outdoor/at-scale real flight** additionally requires RTK, the PX4-side
failsafes, and a completed L5 hardware checklist ([HARDWARE.md](HARDWARE.md)).

**Sign-off:** _Engineering ___ · QA ___ · Release ___ · Date ____
