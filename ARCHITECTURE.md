# Skyforge Architecture

Two halves with a serialized data contract between them:

```
            OFFLINE COMPILER (compiler/, core/)                         ONLINE RUNTIME (runtime/)
 ShowBuilder → trajectories → layer/deconflict → envelopes → validate → .skyforge ─► load+gate → fly on PX4 SITL
```

The compiler is pure/deterministic and hardware-free; the runtime drives PX4 via MAVSDK/gRPC.

## Offline compiler

`CompilePipeline.run(builder)` (`compiler/pipeline.py`) executes:

1. **Build** — `ShowBuilder.compile()` (`compiler/show_builder.py`) turns acts (formation +
   transition + hold) into per-drone `(t, Vec3)` waypoints: takeoff → each act → landing.
   - **Formations** (`compiler/formations.py`) generate per-fleet offsets and **auto-scale**
     (`_fit_min_spacing`, arc-length `spiral`) so `n` drones clear the planned separation.
   - **Assignment** (`compiler/assignment.py`) — `assign_nocross`: Hungarian (min total distance)
     + greedy crossing-swap + a **time-parameterised separation-repair** (closed-form closest
     approach) that fixes collinear/same-line collisions a pure crossing test misses.
   - **Altitude-layered transitions** — `band_assignment` graph-colours drones whose horizontal
     paths conflict into vertical **bands**; `_append_transition` emits a **phase-separated** move:
     climb straight up at the start slot → cross horizontally at the band altitude → descend
     straight down at the target slot. Different bands stay ≥ `layer_spacing` apart during the
     cross, and the vertical legs happen over ≥ `min_sep`-spaced slots → collision-free by
     construction for correctly-banded drones. Conflict-free shows stay flat (band 0).
   - **Trajectory fit** (`compiler/trajectory_generator.py`) — hold-aware **cubic Hermite**:
     zero velocity at holds and at the takeoff/land ends, so drones actually *stop* in formation
     instead of overshooting through neighbours.
2. **Deconflict** (`compiler/deconflict.py`) — residual polish. Vectorised detection; injects
   lateral corrections; a **divergence guard** bails (and the pipeline fast-fails) rather than
   looping forever on a dense field. Now operates on the *sparse* residual the layering leaves,
   where it converges. (An optional convergent planner, `compiler/verified_layering.py`, can be
   enabled via `CompileConfig.verified_layering` for shows where this diverges.)
3. **Envelopes** (`compiler/envelope.py`) — per-drone max safe deviation radius over time.
4. **Validate** (`compiler/validator.py`) — separation (errors < `min_sep`), speed, temporal
   coverage, reactive bindings, LED tracks, envelopes. Passing stamps `validation_status="validated"`.

Sampling for stages 2–4 is vectorised via `compiler/sampling.py` (`sample_positions` →
`(n, T, 3)` NumPy, exact match to `NominalTrajectory.evaluate`) — the 100-drone compile is ~1 s.

## Show format (`core/show_format/`)

`ShowFile` = metadata + per-drone `NominalTrajectory` (piecewise cubic `PolySegment`s) + LED
tracks + safety envelopes + reactive bindings. Serialized to JSON (human-readable) and msgpack.

`ShowMetadata` carries the **compile-time safety contract** (schema v2): `validation_status`,
`compile_min_sep_m`, `compile_deconflict_hz`, `compile_validate_hz`, `deconflicted`,
`deconflict_resolved`, `envelopes_computed`. `reader.py` **rejects structurally-malformed shows
on load** (NaN/inf coeffs, length mismatch, bad ids, non-positive duration) so corruption can't
reach the runtime as "validated".

## Collision avoidance — two layers

- **Offline (planning):** assignment + altitude-layered phase-separated transitions + formation
  scaling + deconfliction. This is what *guarantees* the validated separation.
- **Online (reactive):** `runtime/show/apf.py` — 3D velocity-aware Artificial Potential Field.
  Gradual repulsion within `APF_D0`; emergency max-repulsion (aggregated over **all** too-close
  neighbours, horizontal + vertical) below `APF_MIN_SEP_M`; bounded per-drone symmetry-breaking
  perturbation. APF corrects runtime deviation; it is not the primary separation guarantee.

## Compile → runtime safety handshake

`runtime/show/config.py` is the **single source of truth** for the runtime:

- `MIN_SEP_M` (= the compiler's `min_sep_m`); ports `MAVLINK_BASE`/`GRPC_BASE`/`GCS_BEACON_*`.
- `APF_MIN_SEP_M = MIN_SEP_M − APF_EMERGENCY_BUFFER_M` — invariant `APF_MIN_SEP_M < MIN_SEP_M`
  (emergency is a last-resort floor *below* the planned separation).

`run_skyforge.py` on load: **refuses to fly unless `validation_status == "validated"`**
(`--allow-unvalidated` overrides), and warns if the show's `compile_min_sep_m` differs from the
runtime `MIN_SEP_M` or if it was compiled with unresolved conflicts.

## Online runtime (`runtime/`)

Two modes (see `runtime/CLAUDE.md` for ops):

- **Skyforge player** (`run_skyforge.py` + `show/skyforge_adapter.py`) — loads a `ShowFile`, syncs
  drones on `show_start_event`, evaluates polynomials + reactive offsets + APF at 10 Hz.
- **Interactive commander** (`run_commander.py` + `commander/`) — live REPL; per-drone coroutines
  track interpolated formation targets; multi-flight cycle. A `formation` change reuses the
  compiler's collision-free planning *live*: scale the formation to the fleet + `assign_nocross`
  (targeting ~2.5 m clearance for tracking-error margin) so dense pattern changes don't fly drones
  through each other; APF is the reactive backstop. It stays single-altitude (no offline-style
  altitude layering — a fast live transition can't make the vertical reconverge PX4-feasible).
  `takeoff` rises drones **in place** (keeps current XY) rather than converging the fleet to home.

MAVSDK/PX4 wiring: one `mavsdk_server` per drone (gRPC `GRPC_BASE+i`, MAVLink UDP `MAVLINK_BASE+i`)
plus a **GCS beacon** on `GCS_BEACON_MAVLINK` (PX4 SITL hard-codes `remote=14550` and denies arm
without it). `_wait_healthy` gates on EKF global+home position before arming; the arm itself is
crash-hardened (`_ensure_ready` re-checks readiness and respawns a server that aborts mid-arm).
Connect tolerates partial-fleet failure (respawn/retry; fly the drones that came up).

**Deployment profile** (`show/connection.py`, `load_profile`): the *same* runtime targets SITL,
HITL, or real PX4 hardware by configuration. With no env set it's the exact SITL wiring above; a
`$SKYFORGE_FLEET` JSON file instead gives each drone a real MAVLink endpoint (`serial://…`,
`udp://host:port`) — only that endpoint string changes, the gRPC side stays local — and can disable
the SITL-only GCS beacon (`use_gcs_beacon:false`). LEDs go through a pluggable **backend**
(`show/led_backend.py`, `$SKYFORGE_LED_BACKEND`): Gazebo for SITL, a no-op stub (or a driver you
add) for hardware. See `docs/HARDWARE.md` / `docs/HITL.md` — geodetic origin and on-vehicle
safety/failsafes are the documented remaining gaps.

### Critical runtime invariant

A MAVSDK telemetry generator must **never** be wrapped in `asyncio.wait_for` — cancelling a
pending `__anext__()` permanently breaks the stream. Position/velocity is consumed by a dedicated
`telemetry_consumer` task (plain `async for`) into a cache; the control loop only reads the cache.
The LED Gazebo backend bounds its `gz service` subprocesses with a semaphore so a colour change
can't starve the offboard setpoint stream.

## Testing

`pytest -q` — compiler (formations, assignment, deconfliction, envelopes, validator, schema
round-trip + malformed-input) and runtime logic (APF 3D/emergency aggregation; telemetry-consumer
cache/re-subscribe/error-recovery). Async tests drive bodies via `asyncio.run` and stub MAVSDK, so
no hardware or `pytest-asyncio` is required.
