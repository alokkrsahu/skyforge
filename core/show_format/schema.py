"""
Skyforge show file schema  — v1
All positions in NED (metres) relative to the venue origin.
All times in seconds from show t=0.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Optional

SCHEMA_VERSION = 2   # v2: ShowMetadata carries the compile-time safety contract


# ── Primitives ────────────────────────────────────────────────────────────────

@dataclass
class Vec3:
    n: float = 0.0   # North  (m)
    e: float = 0.0   # East   (m)
    d: float = 0.0   # Down   (m, negative = up)

    def __add__(self, o: Vec3) -> Vec3:
        return Vec3(self.n + o.n, self.e + o.e, self.d + o.d)

    def __sub__(self, o: Vec3) -> Vec3:
        return Vec3(self.n - o.n, self.e - o.e, self.d - o.d)

    def __mul__(self, s: float) -> Vec3:
        return Vec3(self.n * s, self.e * s, self.d * s)


@dataclass
class Color:
    r: float = 1.0
    g: float = 1.0
    b: float = 1.0
    a: float = 1.0


# ── Venue & drone metadata ────────────────────────────────────────────────────

@dataclass
class VenueOrigin:
    """Geographic anchor for the show's NED frame."""
    latitude:  float = 0.0   # degrees
    longitude: float = 0.0   # degrees
    altitude:  float = 0.0   # metres MSL
    heading:   float = 0.0   # degrees true north (NED x-axis direction)


@dataclass
class DroneSpec:
    logical_id:   int    # 0-based; decoupled from physical serial
    home_ned:     Vec3   # home position in NED from venue origin
    vehicle_type: str = "x500"


# ── Trajectory ────────────────────────────────────────────────────────────────

@dataclass
class PolySegment:
    """
    One piece of a piecewise polynomial trajectory.

    p(t) = Σ_k  coeffs[k] * (t - t_start)^k

    Coefficients stored in INCREASING power order: coeffs[0] = constant term.
    Cubic spline → 4 coefficients. Quintic → 6. Degree detected from len(coeffs).
    """
    t_start:  float
    t_end:    float
    coeffs_n: list[float]
    coeffs_e: list[float]
    coeffs_d: list[float]

    def evaluate(self, t: float) -> Vec3:
        dt = max(0.0, min(t - self.t_start, self.t_end - self.t_start))
        n = e = d = 0.0
        dt_k = 1.0
        for cn, ce, cd in zip(self.coeffs_n, self.coeffs_e, self.coeffs_d):
            n += cn * dt_k
            e += ce * dt_k
            d += cd * dt_k
            dt_k *= dt
        return Vec3(n, e, d)

    def evaluate_velocity(self, t: float) -> Vec3:
        """First derivative dp/dt."""
        dt = max(0.0, min(t - self.t_start, self.t_end - self.t_start))
        n = e = d = 0.0
        dt_k = 1.0
        for k, (cn, ce, cd) in enumerate(
            zip(self.coeffs_n[1:], self.coeffs_e[1:], self.coeffs_d[1:]), start=1
        ):
            n += k * cn * dt_k
            e += k * ce * dt_k
            d += k * cd * dt_k
            dt_k *= dt
        return Vec3(n, e, d)


@dataclass
class NominalTrajectory:
    drone_id: int
    segments: list[PolySegment] = field(default_factory=list)

    def evaluate(self, t: float) -> Vec3:
        if not self.segments:
            return Vec3()
        if t <= self.segments[0].t_start:
            return self.segments[0].evaluate(self.segments[0].t_start)
        if t >= self.segments[-1].t_end:
            return self.segments[-1].evaluate(self.segments[-1].t_end)
        for seg in self.segments:
            if seg.t_start <= t <= seg.t_end:
                return seg.evaluate(t)
        return self.segments[-1].evaluate(t)


# ── LED ───────────────────────────────────────────────────────────────────────

@dataclass
class LedKeyframe:
    t:     float
    color: Color


@dataclass
class LedTrack:
    drone_id:  int
    keyframes: list[LedKeyframe] = field(default_factory=list)

    def evaluate(self, t: float) -> Color:
        """RGBA linear interpolation between keyframes."""
        if not self.keyframes:
            return Color()
        if t <= self.keyframes[0].t:
            return self.keyframes[0].color
        if t >= self.keyframes[-1].t:
            return self.keyframes[-1].color
        for k0, k1 in zip(self.keyframes, self.keyframes[1:]):
            if k0.t <= t <= k1.t:
                a = (t - k0.t) / (k1.t - k0.t)
                c0, c1 = k0.color, k1.color
                return Color(
                    c0.r + a * (c1.r - c0.r),
                    c0.g + a * (c1.g - c0.g),
                    c0.b + a * (c1.b - c0.b),
                    c0.a + a * (c1.a - c0.a),
                )
        return self.keyframes[-1].color


# ── Envelope (Phase 2 will fill these in) ─────────────────────────────────────

@dataclass
class EnvelopeSegment:
    t_start:  float
    t_end:    float
    radius_m: float = 0.0   # Phase 2: computed by envelope_derivation


@dataclass
class DroneEnvelope:
    drone_id: int
    segments: list[EnvelopeSegment] = field(default_factory=list)

    def radius_at(self, t: float) -> float:
        for seg in self.segments:
            if seg.t_start <= t <= seg.t_end:
                return seg.radius_m
        return 0.0


# ── Reactive bindings ─────────────────────────────────────────────────────────

@dataclass
class ReactiveBinding:
    """
    Declarative reactive binding — NO executable code in the show file.
    The runtime resolves 'primitive' to a registered function.

    input_source : "music_beat" | "music_energy" | "director_intensity" | ...
    primitive    : "oscillate_on_beat" | "expand_on_energy" | "hold_steady" | ...
    parameters   : primitive-specific dict (validated against primitive schema)
    drone_ids    : [] means all drones
    """
    input_source: str
    primitive:    str
    parameters:   dict
    t_start:      float
    t_end:        float
    drone_ids:    list[int] = field(default_factory=list)


# ── Top-level ─────────────────────────────────────────────────────────────────

@dataclass
class ShowMetadata:
    schema_version:    int   = SCHEMA_VERSION
    name:              str   = "Untitled Show"
    author:            str   = ""
    created_at:        str   = field(
        default_factory=lambda: _time.strftime('%Y-%m-%dT%H:%M:%SZ', _time.gmtime())
    )
    venue_name:        str   = ""
    origin:            VenueOrigin = field(default_factory=VenueOrigin)
    duration_s:        float = 0.0
    n_drones:          int   = 0
    validation_status: str   = "unvalidated"   # unvalidated | validated | signed
    # ── Compile-time safety contract (schema v2) ──────────────────────────────
    # Persisted so the runtime can verify it is flying a show that was planned &
    # validated under separation assumptions compatible with its own. 0.0 / False
    # mean "unknown" — i.e. a show compiled before these fields existed.
    compile_min_sep_m:     float = 0.0   # hard separation the show was validated for (m)
    compile_deconflict_hz: float = 0.0   # trajectory-deconfliction sampling rate (Hz)
    compile_validate_hz:   float = 0.0   # validation sampling rate (Hz)
    deconflicted:          bool  = False # did trajectory deconfliction run?
    deconflict_resolved:   bool  = True  # False = residual separation conflicts remain


@dataclass
class ShowFile:
    metadata:          ShowMetadata
    drones:            list[DroneSpec]
    trajectories:      list[NominalTrajectory]
    led_tracks:        list[LedTrack]
    envelopes:         list[DroneEnvelope]
    reactive_bindings: list[ReactiveBinding]
    audio_file:        Optional[str] = None
