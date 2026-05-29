# Skyforge

A drone-show platform: an **offline compiler** that turns a high-level show script into
validated, collision-free flight trajectories, and an **online runtime** that flies them on
PX4 SITL via MAVSDK.

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

Formations: `circle`, `grid`, `line`, `v_shape`, `star`, `spiral`, `text:HELLO`,
`grid:spacing=4`, or an explicit list of `(dN, dE)` offsets. Formations auto-scale so the fleet
clears the planned separation.

## Fly it (PX4 SITL — three terminals)

```bash
./t1_sitl.sh 16            # Terminal 1 — PX4 SITL ×16 + Gazebo (headless)
./t2_gazebo_gui.sh         # Terminal 2 — 3D view
./t5_skyforge.sh ../shows/my_show.skyforge.json   # Terminal 3 — fly the compiled show
# or:  ./t6_commander.sh 16                        #            — live interactive REPL
```

The runtime **refuses to fly a show that isn't `validated`** (pass `--allow-unvalidated` to
`run_skyforge.py` to override). See `runtime/CLAUDE.md` for the MAVSDK/port wiring and operational
gotchas.

## Testing

```bash
pytest -q
```

Covers the compiler (formations, assignment, deconfliction, envelopes, validation, schema
round-trip + malformed-input) and the runtime telemetry/APF logic (async tests stub MAVSDK).

## Layout

| Path | What |
|------|------|
| `cli.py` | `skyforge` CLI (compile / validate / info) |
| `compiler/` | show builder, formations, assignment, trajectory fit, layering, deconfliction, envelopes, validator, pipeline |
| `core/show_format/` | `ShowFile` schema + JSON/msgpack IO |
| `core/reactive/` | declarative reactive primitives |
| `runtime/` | flight runtime — `run_skyforge.py` (player), `run_commander.py` (live), `show/`, `commander/`, launch scripts |
| `shows/` | example show scripts |
| `tests/` | unit + integration tests |
