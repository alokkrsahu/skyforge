#!/usr/bin/env python3
"""
Drone light controller — change colors, turn on/off, adjust brightness at runtime.
Simulation must be running (t1_sitl.sh + t2_gazebo_gui.sh).

Usage:
  python3 drone_lights.py <drone> <command> [args]

  drone    : 0, 1, 2, 3  or  all

Commands:
  on                     Turn all lights on
  off                    Turn all lights off
  color <r> <g> <b>      Set color  (float 0.0–1.0 each)
  preset <name>          Apply named preset
  brightness <0.0–1.0>   Scale intensity

Presets: red  green  blue  white  yellow  cyan  magenta  orange  purple

Examples:
  python3 drone_lights.py all off
  python3 drone_lights.py 0 preset red
  python3 drone_lights.py 1 color 0.0 0.5 1.0
  python3 drone_lights.py all brightness 0.3
  python3 drone_lights.py 2 on
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from show.gz_world import resolve_gz_world

# Each (front/rear) point light on the arm tips
ARM_LIGHTS = [
    "light_front_left",
    "light_front_right",
    "light_rear_left",
    "light_rear_right",
]

PRESETS = {
    "red":     (0.9, 0.0, 0.0),
    "green":   (0.0, 0.9, 0.0),
    "blue":    (0.0, 0.3, 1.0),
    "white":   (1.0, 1.0, 1.0),
    "yellow":  (1.0, 1.0, 0.0),
    "cyan":    (0.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "orange":  (1.0, 0.45, 0.0),
    "purple":  (0.6, 0.0, 1.0),
}

# Physical defaults matching the SDF values we set
POINT_DEFAULTS = "range: 5.0 attenuation_constant: 0.3 attenuation_linear: 0.2 attenuation_quadratic: 0.01"
SPOT_DEFAULTS  = ("range: 15.0 attenuation_constant: 0.3 attenuation_linear: 0.05 "
                  "attenuation_quadratic: 0.001 spot_inner_angle: 0.3 spot_outer_angle: 0.8 spot_falloff: 1.0")


_WORLD = None   # resolved gz world name, cached on first publish (not at import)


def _light_topic() -> str:
    global _WORLD
    if _WORLD is None:
        _WORLD = resolve_gz_world()
    return f"/world/{_WORLD}/light_config"


def _publish(proto: str):
    result = subprocess.run(
        ["gz", "topic", "-t", _light_topic(),
         "-m", "gz.msgs.Light", "-p", proto],
        capture_output=True, text=True
    )
    if result.returncode != 0 and result.stderr:
        print(f"  gz warning: {result.stderr.strip()}", file=sys.stderr)


def _set_arm_lights(model: str, r: float, g: float, b: float,
                    off: bool = False, intensity: float = 1.0):
    """Set all four arm-tip point lights on a drone."""
    sr, sg, sb = r * 0.3, g * 0.3, b * 0.3
    off_str = "true" if off else "false"
    for light in ARM_LIGHTS:
        name = f"{model}::base_link::{light}"
        proto = (
            f'name: "{name}" type: POINT '
            f'diffuse {{r: {r} g: {g} b: {b} a: 1.0}} '
            f'specular {{r: {sr:.3f} g: {sg:.3f} b: {sb:.3f} a: 1.0}} '
            f'{POINT_DEFAULTS} intensity: {intensity} is_light_off: {off_str}'
        )
        _publish(proto)


def _set_spotlight(model: str, r: float, g: float, b: float,
                   off: bool = False, intensity: float = 1.0):
    """Set the downward spotlight on a drone."""
    sr, sg, sb = r * 0.5, g * 0.5, b * 0.5
    off_str = "true" if off else "false"
    name = f"{model}::base_link::spotlight_down"
    proto = (
        f'name: "{name}" type: SPOT '
        f'diffuse {{r: {r} g: {g} b: {b} a: 1.0}} '
        f'specular {{r: {sr:.3f} g: {sg:.3f} b: {sb:.3f} a: 1.0}} '
        f'direction {{x: 0.0 y: 0.0 z: -1.0}} '
        f'{SPOT_DEFAULTS} intensity: {intensity} is_light_off: {off_str}'
    )
    _publish(proto)


def set_drone(drone_id: int, r: float, g: float, b: float,
              off: bool = False, intensity: float = 1.0):
    model = f"x500_{drone_id}"
    _set_arm_lights(model, r, g, b, off=off, intensity=intensity)
    _set_spotlight(model, r, g, b, off=off, intensity=intensity)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    target  = sys.argv[1].lower()
    command = sys.argv[2].lower()

    drones = list(range(4)) if target == "all" else [int(target)]

    if command == "off":
        for d in drones:
            set_drone(d, 0, 0, 0, off=True)
        print(f"Lights OFF  — drone(s): {drones}")

    elif command == "on":
        for d in drones:
            set_drone(d, 1.0, 1.0, 1.0, off=False)
        print(f"Lights ON   — drone(s): {drones}")

    elif command == "color":
        if len(sys.argv) < 6:
            print("Usage: drone_lights.py <drone> color <r> <g> <b>   (0.0–1.0 each)")
            sys.exit(1)
        r, g, b = float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
        for d in drones:
            set_drone(d, r, g, b)
        print(f"Color ({r:.2f}, {g:.2f}, {b:.2f}) — drone(s): {drones}")

    elif command == "preset":
        if len(sys.argv) < 4 or sys.argv[3].lower() not in PRESETS:
            print(f"Available presets: {', '.join(PRESETS)}")
            sys.exit(1)
        r, g, b = PRESETS[sys.argv[3].lower()]
        for d in drones:
            set_drone(d, r, g, b)
        print(f"Preset '{sys.argv[3]}' — drone(s): {drones}")

    elif command == "brightness":
        if len(sys.argv) < 4:
            print("Usage: drone_lights.py <drone> brightness <0.0–1.0>")
            sys.exit(1)
        intensity = max(0.0, min(1.0, float(sys.argv[3])))
        for d in drones:
            set_drone(d, 1.0, 1.0, 1.0, intensity=intensity)
        print(f"Brightness {intensity:.2f} — drone(s): {drones}")

    else:
        print(f"Unknown command: '{command}'")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
