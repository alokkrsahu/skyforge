"""
Hundred-drone sky canvas show.

Formations used:
  grid   → takeoff spread (10×10)
  circle → ring reveal + beat-sync wave
  star   → 5-point star
  text:ALOK → sky art (59 pixels + 41 outer-ring drones)
  spiral → galactic wind-down
  v_shape → arrowhead finale
  grid   → reform and land

Run to compile:
    python3 -m shows.hundred_drone_demo

Set N_DRONES=4 (or any smaller value) for a quick SITL test with
the same choreography scaled to a smaller fleet.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from compiler.formations import pixel_count
from compiler.show_builder import ShowBuilder
from core.show_format.schema import Color, DroneSpec, Vec3, VenueOrigin

N = int(os.environ.get("N_DRONES", "100"))

# ── Fleet: N×N grid spawn, 2 m spacing ───────────────────────────────────────
_COLS = math.ceil(math.sqrt(N))
DRONES = [
    DroneSpec(
        logical_id = i,
        home_ned   = Vec3(n=2.0 * (i // _COLS), e=2.0 * (i % _COLS)),
    )
    for i in range(N)
]

_cx = (_COLS - 1) * 1.0        # centre of spawn grid (NED N)
_cy = (_COLS - 1) * 1.0        # centre of spawn grid (NED E)

# ── Build show ────────────────────────────────────────────────────────────────
builder = ShowBuilder(
    name   = f"Hundred-Drone Sky Canvas ({N} drones)",
    drones = DRONES,
    origin = VenueOrigin(latitude=0.0, longitude=0.0, altitude=0.0, heading=0.0),
    author = "Skyforge",
    venue  = "PX4 SITL",
)

(builder
 # Act 1: rise in place — grid spread near spawn
 .add_act("grid",      center_ne=(_cx, _cy),  transition_s=15.0, hold_s=5.0)
 # Act 2: expand to large circle
 .add_act("circle",    center_ne=(_cx, _cy),  transition_s=15.0, hold_s=10.0)
 # Act 3: morph into star
 .add_act("star",      center_ne=(_cx, _cy),  transition_s=12.0, hold_s=8.0)
 # Act 4: sky-art — spell "ALOK" (59 pixels + 41 frame drones)
 .add_act("text:alok", center_ne=(_cx, _cy),  transition_s=20.0, hold_s=12.0)
 # Act 5: spiral wind-down
 .add_act("spiral",    center_ne=(_cx, _cy),  transition_s=15.0, hold_s=6.0)
 # Act 6: V-shape arrowhead finale
 .add_act("v_shape",   center_ne=(_cx, _cy),  transition_s=12.0, hold_s=6.0)
 # Act 7: reform circle before descent
 .add_act("circle",    center_ne=(_cx, _cy),  transition_s=12.0, hold_s=5.0)
 # Act 8: compress back to landing grid
 .add_act("grid",      center_ne=(_cx, _cy),  transition_s=15.0, hold_s=5.0)
)

# ── LED colour timeline ───────────────────────────────────────────────────────
# Act timing (approx): takeoff 15 s, then each act starts at cumulative time
builder.add_led_cue(t=0.0,   color=Color(1.0, 1.0, 1.0))   # white  — takeoff
builder.add_led_cue(t=15.0,  color=Color(0.0, 0.8, 0.0))   # green  — grid
builder.add_led_cue(t=35.0,  color=Color(0.0, 0.4, 1.0))   # blue   — circle
builder.add_led_cue(t=63.0,  color=Color(1.0, 0.8, 0.0))   # gold   — star
builder.add_led_cue(t=83.0,  color=Color(1.0, 0.0, 0.8))   # magenta — ALOK text
builder.add_led_cue(t=115.0, color=Color(0.0, 1.0, 0.8))   # cyan   — spiral
builder.add_led_cue(t=136.0, color=Color(1.0, 0.4, 0.0))   # orange — V-shape
builder.add_led_cue(t=154.0, color=Color(0.4, 0.0, 1.0))   # purple — circle
builder.add_led_cue(t=171.0, color=Color(1.0, 1.0, 1.0))   # white  — landing

# ── Reactive bindings ─────────────────────────────────────────────────────────

# Circle act (t≈35–63): Mexican wave — each drone's beat is offset by drone_id.
# phase_per_drone=0.05 gives drone k a phase of 0.05k beats ahead of drone 0,
# so across 100 drones the wave completes 5 full cycles around the ring.
builder.add_reactive_binding(
    input_source = "music_beat",
    primitive    = "oscillate_on_beat",
    parameters   = {"amplitude_m": 0.8, "decay": 3.0, "bpm": 120.0,
                    "phase_per_drone": 0.05},
    t_start      = 35.0,
    t_end        = 63.0,
    drone_ids    = [],   # all drones
)

# Star act (t≈63–83): slower pulse, bigger amplitude
builder.add_reactive_binding(
    input_source = "music_beat",
    primitive    = "oscillate_on_beat",
    parameters   = {"amplitude_m": 1.2, "decay": 4.0, "bpm": 60.0},
    t_start      = 63.0,
    t_end        = 83.0,
    drone_ids    = [],
)

# ALOK text act (t≈83–115): gentle shimmer so the letters "breathe"
builder.add_reactive_binding(
    input_source = "music_beat",
    primitive    = "phase_ripple",
    parameters   = {"amplitude_m": 0.5, "frequency_hz": 0.5,
                    "phase_rad": 0.0, "bpm": 90.0,
                    "phase_per_drone": 0.08},
    t_start      = 83.0,
    t_end        = 115.0,
    drone_ids    = [],
)

# ── Compile & write ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    from compiler.pipeline import CompileConfig, CompilePipeline
    from core.show_format.writer import to_json, to_msgpack

    print(f"\n{'='*55}")
    print(f"  Hundred-Drone Sky Canvas — {N} drones")
    print(f"  pixel_count('ALOK') = {pixel_count('ALOK')} drones for text")
    print(f"  Extra drones orbiting text = {max(0, N - pixel_count('ALOK'))}")
    print(f"{'='*55}\n")

    pipeline = CompilePipeline(CompileConfig(deconflict=False, fail_on_error=False))
    result   = pipeline.run(builder)
    show     = result.show

    if result.validation:
        print(result.validation)

    out_dir   = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(out_dir, "hundred_drone_demo.skyforge.json")
    bin_path  = os.path.join(out_dir, "hundred_drone_demo.skyforge")

    to_json(show, json_path)
    to_msgpack(show, bin_path)

    print(f"\nCompiled: {show.metadata.n_drones} drones, "
          f"{show.metadata.duration_s:.1f}s, "
          f"{sum(len(t.segments) for t in show.trajectories)} total segments")
    print(f"  Status → {show.metadata.validation_status}")
    print(f"  JSON   → {json_path}")
    print(f"  Binary → {bin_path}")
