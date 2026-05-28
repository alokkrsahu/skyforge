"""
Reactive primitive library.
Each primitive is a pure function: (parameters, input_value, t) → (dN, dE, dD).
The returned offset is guaranteed to satisfy |offset| ≤ envelope_radius when
input_value ∈ [0, 1] and parameters are within their validated ranges.
"""
from __future__ import annotations

import math
from typing import Callable

# Registry: primitive_name → function
_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    def decorator(fn):
        _REGISTRY[name] = fn
        return fn
    return decorator


def get(name: str) -> Callable:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown reactive primitive: '{name}'. "
                       f"Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def evaluate(primitive: str, parameters: dict, input_value: float, t: float,
             envelope_radius: float) -> tuple[float, float, float]:
    """Evaluate a primitive and clamp output to envelope_radius."""
    fn = get(primitive)
    dN, dE, dD = fn(parameters, input_value, t)
    mag = math.sqrt(dN**2 + dE**2 + dD**2)
    if mag > envelope_radius and mag > 0:
        scale = envelope_radius / mag
        dN, dE, dD = dN * scale, dE * scale, dD * scale
    return dN, dE, dD


# ── Primitive implementations ─────────────────────────────────────────────────

@register("hold_steady")
def hold_steady(params: dict, input_value: float, t: float):
    """No deviation from nominal. Default when no reactivity desired."""
    return 0.0, 0.0, 0.0


@register("oscillate_on_beat")
def oscillate_on_beat(params: dict, input_value: float, t: float):
    """
    Oscillate vertically on each beat pulse.
    input_value: beat pulse intensity [0, 1].
    params:
      amplitude_m  (float, default 1.0): max vertical deviation in metres
      decay        (float, default 4.0): how fast the pulse decays (1/s)
    """
    amplitude = params.get("amplitude_m", 1.0)
    decay     = params.get("decay", 4.0)
    dD = -amplitude * input_value * math.exp(-decay * input_value)
    return 0.0, 0.0, dD


@register("expand_on_energy")
def expand_on_energy(params: dict, input_value: float, t: float):
    """
    Radially expand/contract formation based on audio energy.
    Drone moves outward from show centre proportional to energy.
    input_value: normalized energy [0, 1].
    params:
      max_radius_m (float, default 2.0): max outward expansion
      drone_angle  (float, required): angle (rad) of this drone from centre,
                   set by compiler per-drone so each expands in its own direction
    """
    max_r = params.get("max_radius_m", 2.0)
    angle = params.get("drone_angle", 0.0)
    r     = max_r * input_value
    return r * math.cos(angle), r * math.sin(angle), 0.0


@register("disperse_from_center")
def disperse_from_center(params: dict, input_value: float, t: float):
    """
    Move radially outward from formation centre.
    input_value: dispersion amount [0, 1].
    params:
      max_radius_m (float, default 3.0)
      drone_angle  (float, required): per-drone radial direction
    """
    max_r = params.get("max_radius_m", 3.0)
    angle = params.get("drone_angle", 0.0)
    r     = max_r * input_value
    return r * math.cos(angle), r * math.sin(angle), 0.0


@register("track_point")
def track_point(params: dict, input_value: float, t: float):
    """
    Move towards a target point streamed at runtime.
    input_value: [0, 1] proximity to target (1 = at target, 0 = at envelope edge).
    params:
      max_offset_m (float, default 2.0): max movement towards target
      direction_n  (float): North component of unit vector towards target
      direction_e  (float): East component
    The compiler/runtime updates direction_n/direction_e each tick.
    """
    max_offset = params.get("max_offset_m", 2.0)
    dN = params.get("direction_n", 0.0) * max_offset * input_value
    dE = params.get("direction_e", 0.0) * max_offset * input_value
    return dN, dE, 0.0


@register("phase_ripple")
def phase_ripple(params: dict, input_value: float, t: float):
    """
    Sinusoidal ripple with per-drone phase offset — creates wave effect.
    input_value: ripple intensity [0, 1].
    params:
      amplitude_m  (float, default 1.0)
      frequency_hz (float, default 1.0)
      phase_rad    (float, required): per-drone phase offset set by compiler
    """
    amp   = params.get("amplitude_m", 1.0)
    freq  = params.get("frequency_hz", 1.0)
    phase = params.get("phase_rad", 0.0)
    dD    = -amp * input_value * math.sin(2 * math.pi * freq * t + phase)
    return 0.0, 0.0, dD
