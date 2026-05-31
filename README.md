# Skyforge

A drone-show platform: an **offline compiler** that turns a high-level show script into
validated, collision-free flight trajectories, and an **online runtime** that flies them on
PX4 via MAVSDK — SITL today, with a config-driven path to HITL and real hardware
(see [docs/HARDWARE.md](docs/HARDWARE.md)).

```
show script (Python)
   │  skyforge compile
   ▼
.skyforge.json / .skyforge   ──►   PX4 SITL + Gazebo   (runtime: skyforge player or live commander)
(piecewise-cubic trajectories,
 LED tracks, safety envelopes,
 validated + safety-contract metadata)
```

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the design and **[runtime/CLAUDE.md](runtime/CLAUDE.md)**
for running the flight stack.

## Install

```bash
python3 -m pip install -e ".[dev]"     # numpy, scipy, msgpack (+ pytest, pytest-asyncio)
```

Requires Python ≥ 3.11. The flight runtime additionally needs a built PX4 SITL + Gazebo and the
`mavsdk` Python package (these live in the PX4 virtualenv, not in the compiler's deps).

## Quickstart — compile & validate (no hardware)

A show script defines a module-level `builder` (a `ShowBuilder`). To compile, validate, and inspect:

```bash
skyforge compile  shows/four_drone_demo.py        # → shows/four_drone_demo.skyforge[.json]
skyforge validate shows/four_drone_demo.skyforge.json
skyforge info     shows/four_drone_demo.skyforge.json
```

`compile` runs the full pipeline (assign → layer/deconflict → envelopes → validate) and **only
writes output if validation passes**. Authoring a show:

```python
from compiler.show_builder import ShowBuilder
from core.show_format.schema import Color, DroneSpec, Vec3

drones  = [DroneSpec(i, Vec3(n=2.0*(i//4), e=2.0*(i%4))) for i in range(16)]
builder = ShowBuilder("My Show", drones)
(builder
 .add_act("circle", center_ne=(8, 8), transition_s=12, hold_s=6)
 .add_act("text:HELLO", center_ne=(8, 8), transition_s=15, hold_s=10))
builder.add_led_cue(t=0, color=Color(0, 0.8, 0))
```

Formations: `circle`, `grid`, `line`, `v_shape`, `star`, `spiral`, `text:HELLO`, or an explicit
list of `(dN, dE[, dU])` offsets. Formations auto-scale so the fleet clears the planned
separation. Data patterns may carry a third column `dU` (up) for **volumetric 3D sculptures**
(e.g. `cat`) — legible from the ground, not just overhead; flat patterns omit it. They're a
**plugin package** (`compiler/formations/`): one file per pattern under `patterns/`
(code `.py` or data `.csv`/`.json`) — drop a file and it's instantly usable, no other edits. See
[`compiler/formations/patterns/README.md`](compiler/formations/patterns/README.md).

## Fly it (PX4 SITL — three terminals)

```bash
./t1_sitl.sh 16            # Terminal 1 — PX4 SITL ×16 + Gazebo (headless)
./t1_sitl.sh 16 walls      #   …or pick an arena (./t1_sitl.sh -h lists them)
./t2_gazebo_gui.sh         # Terminal 2 — 3D view
./t7_qgc.sh                # Terminal 7 — optional: QGroundControl monitor (then SKYFORGE_GCS=qgc below)
./t5_skyforge.sh ../shows/my_show.skyforge.json   # Terminal 3 — fly the compiled show
# or:  ./t6_commander.sh 16                        #            — live interactive REPL
```

Only `t1` takes the arena (it sets `PX4_GZ_WORLD`); `t2` and the runtime auto-detect the running
world. The `default` arena is the forest stage (DART, 100+ drones); stock worlds are ODE and warn
above ~40 drones. To monitor in **QGroundControl**, run `./t7_qgc.sh` and start the show with
`SKYFORGE_GCS=qgc` (QGC then owns the GCS link + heartbeat; Skyforge skips its beacon).

The runtime **refuses to fly a show that isn't `validated`** (pass `--allow-unvalidated` to
`run_skyforge.py` to override). See `runtime/CLAUDE.md` for the MAVSDK/port wiring and operational
gotchas.

### Targeting real PX4 (HITL / hardware)

The same runtime drives real flight controllers **by configuration** — no code change:

```bash
export SKYFORGE_FLEET=fleet.json     # per-drone serial:// or udp:// endpoints; beacon off
export SKYFORGE_LED_BACKEND=stub     # no-op LEDs until a hardware driver is wired
./t6_commander.sh 4
```

With neither env var set it's the SITL wiring above (byte-for-byte). The fleet-file schema,
bring-up checklist, and the remaining gaps (geodetic origin, on-vehicle safety/failsafes) are in
**[docs/HARDWARE.md](docs/HARDWARE.md)**; single-board validation is in
**[docs/HITL.md](docs/HITL.md)**.

## Testing

```bash
pytest -q
```

Covers the compiler (formations, assignment, deconfliction, envelopes, validation, schema
round-trip + malformed-input) and the runtime logic (telemetry/APF, the connection profile, LED
backend, gz-world resolver, arm crash-hardening, and the run-script connect phase — async tests
stub MAVSDK). The full strategy — including the manual SITL / HITL-proxy / hardware procedures — is
in **[docs/TESTING.md](docs/TESTING.md)**.

## Layout

| Path | What |
|------|------|
| `cli.py` | `skyforge` CLI (compile / validate / info) |
| `compiler/` | show builder, formations, assignment, trajectory fit, layering, deconfliction, envelopes, validator, pipeline |
| `core/show_format/` | `ShowFile` schema + JSON/msgpack IO |
| `core/reactive/` | declarative reactive primitives |
| `runtime/` | flight runtime — `run_skyforge.py` (player), `run_commander.py` (live), `show/`, `commander/`, launch scripts |
| `shows/` | example show scripts |
| `docs/` | hardware / HITL deployment guides |
| `tests/` | unit + integration tests |
