"""
Four-drone demo show — migrates the existing prototype into a .skyforge file.
Run this script to regenerate: python3 -m shows.four_drone_demo
Output: four_drone_demo.skyforge.json  (human-readable)
        four_drone_demo.skyforge       (msgpack binary)
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.show_format.schema import Color, DroneSpec, Vec3, VenueOrigin
from compiler.show_builder import ShowBuilder

# ── Drone fleet ───────────────────────────────────────────────────────────────
# Homes match PX4_GZ_MODEL_POSE spawn positions (Gazebo ENU → NED):
#   pose "x,y" → NED N=y, E=x
DRONES = [
    DroneSpec(logical_id=0, home_ned=Vec3(n=0.0, e=0.0)),
    DroneSpec(logical_id=1, home_ned=Vec3(n=0.0, e=2.0)),
    DroneSpec(logical_id=2, home_ned=Vec3(n=2.0, e=0.0)),
    DroneSpec(logical_id=3, home_ned=Vec3(n=2.0, e=2.0)),
]

# ── Build show ────────────────────────────────────────────────────────────────
builder = ShowBuilder(
    name   = "Four-Drone Demo",
    drones = DRONES,
    origin = VenueOrigin(latitude=0.0, longitude=0.0, altitude=0.0, heading=0.0),
    author = "Skyforge",
    venue  = "PX4 SITL",
)

# Seven acts matching the prototype choreography
(builder
 .add_act("grid",    center_ne=(1.0, 1.0),  transition_s=8.0,  hold_s=4.0)
 .add_act("diamond", center_ne=(5.0, 5.0),  transition_s=10.0, hold_s=5.0)
 .add_act("line",    center_ne=(5.0, 5.0),  transition_s=8.0,  hold_s=4.0)
 .add_act("arrow",   center_ne=(3.0, 5.0),  transition_s=8.0,  hold_s=5.0)
 .add_act("diamond", center_ne=(5.0, 5.0),  transition_s=8.0,  hold_s=4.0)
 .add_act("grid",    center_ne=(5.0, 5.0),  transition_s=8.0,  hold_s=4.0)
 .add_act("grid",    center_ne=(1.0, 1.0),  transition_s=10.0, hold_s=3.0)
)

# LED colour timeline — each act gets a different colour
# Takeoff at t=0, first act ends ~t=27 (15s takeoff + 8s trans + 4s hold)
builder.add_led_cue(t=0.0,   color=Color(1.0, 1.0, 1.0))   # white takeoff
builder.add_led_cue(t=15.0,  color=Color(0.0, 0.9, 0.0))   # green — grid
builder.add_led_cue(t=27.0,  color=Color(0.0, 0.3, 1.0))   # blue  — diamond
builder.add_led_cue(t=52.0,  color=Color(1.0, 1.0, 0.0))   # yellow — line
builder.add_led_cue(t=64.0,  color=Color(1.0, 0.5, 0.0))   # orange — arrow
builder.add_led_cue(t=77.0,  color=Color(1.0, 0.0, 1.0))   # magenta — diamond
builder.add_led_cue(t=89.0,  color=Color(0.0, 1.0, 1.0))   # cyan — grid finale
builder.add_led_cue(t=101.0, color=Color(1.0, 1.0, 1.0))   # white — return home

# Demo reactive binding: oscillate on beat during the diamond acts
builder.add_reactive_binding(
    input_source = "music_beat",
    primitive    = "oscillate_on_beat",
    parameters   = {"amplitude_m": 0.5, "decay": 3.0},
    t_start      = 27.0,
    t_end        = 52.0,
    drone_ids    = [],   # all drones
)

# ── Compile & write ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    from compiler.pipeline import CompileConfig, CompilePipeline
    from core.show_format.writer import to_json, to_msgpack

    out_dir  = os.path.dirname(os.path.abspath(__file__))
    pipeline = CompilePipeline(CompileConfig(deconflict=False, fail_on_error=False))
    result   = pipeline.run(builder)
    show     = result.show

    if result.validation:
        print(result.validation)

    json_path = os.path.join(out_dir, "four_drone_demo.skyforge.json")
    bin_path  = os.path.join(out_dir, "four_drone_demo.skyforge")

    to_json(show, json_path)
    to_msgpack(show, bin_path)

    print(f"\nCompiled: {show.metadata.n_drones} drones, "
          f"{show.metadata.duration_s:.1f}s, "
          f"{sum(len(t.segments) for t in show.trajectories)} total segments")
    print(f"  Status → {show.metadata.validation_status}")
    print(f"  JSON   → {json_path}")
    print(f"  Binary → {bin_path}")
