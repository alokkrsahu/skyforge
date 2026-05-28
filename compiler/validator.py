"""
Show file validator  (Phase 2).

validate() runs a battery of checks on a compiled ShowFile and returns a
ValidationResult.  Each check is a private function that appends to the
shared errors / warnings lists; this makes it easy to add or remove checks
without touching the orchestration logic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from core.geometry import distance_3d, norm
from core.reactive.primitives import get as get_primitive
from core.show_format.schema import ShowFile, SCHEMA_VERSION


# ── Result & config value objects ─────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed:   bool
    errors:   list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines  = [status]
        for e in self.errors:
            lines.append(f"  ERROR:   {e}")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)


@dataclass
class ValidationConfig:
    min_sep_m:    float = 1.5    # hard minimum inter-drone separation (error if violated)
    warn_close_m: float = 2.5    # warn if drones come within this distance
    max_speed_ms: float = 15.0   # warn if any axis speed exceeds this
    sample_hz:    float = 10.0   # sampling rate for separation / speed checks


# ── Public entry point ────────────────────────────────────────────────────────

def validate(
    show:   ShowFile,
    config: ValidationConfig | None = None,
) -> ValidationResult:
    """Run all validation checks and return a ValidationResult."""
    if config is None:
        config = ValidationConfig()

    errors:   list[str] = []
    warnings: list[str] = []

    _check_consistency(show, errors, warnings)
    _check_temporal_coverage(show, errors, warnings)
    _check_separation(show, config, errors, warnings)
    _check_speed(show, config, errors, warnings)
    _check_reactive_bindings(show, errors, warnings)
    _check_led_tracks(show, errors, warnings)
    _check_envelopes(show, errors, warnings)

    return ValidationResult(passed=(len(errors) == 0), errors=errors, warnings=warnings)


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_consistency(show: ShowFile, errors: list, warnings: list) -> None:
    n = show.metadata.n_drones
    if show.metadata.schema_version != SCHEMA_VERSION:
        warnings.append(
            f"schema_version {show.metadata.schema_version} != current {SCHEMA_VERSION}"
        )
    if len(show.trajectories) != n:
        errors.append(f"n_drones={n} but {len(show.trajectories)} trajectories")
    if len(show.led_tracks) != n:
        errors.append(f"n_drones={n} but {len(show.led_tracks)} LED tracks")
    if len(show.envelopes) != n:
        errors.append(f"n_drones={n} but {len(show.envelopes)} envelopes")
    if len(show.drones) != n:
        errors.append(f"n_drones={n} but {len(show.drones)} DroneSpecs")
    if show.metadata.duration_s <= 0:
        errors.append(f"duration_s={show.metadata.duration_s} must be > 0")


def _check_temporal_coverage(show: ShowFile, errors: list, warnings: list) -> None:
    tol = 1e-4
    dur = show.metadata.duration_s
    for traj in show.trajectories:
        did = traj.drone_id
        segs = traj.segments
        if not segs:
            errors.append(f"drone {did}: trajectory has no segments")
            continue
        if abs(segs[0].t_start) > tol:
            errors.append(f"drone {did}: first segment starts at {segs[0].t_start:.4f}, expected 0")
        if abs(segs[-1].t_end - dur) > tol:
            errors.append(f"drone {did}: last segment ends at {segs[-1].t_end:.4f}, expected {dur}")
        for k in range(len(segs) - 1):
            gap = abs(segs[k + 1].t_start - segs[k].t_end)
            if gap > tol:
                errors.append(
                    f"drone {did}: gap of {gap:.6f}s between segments {k} and {k+1}"
                )


def _check_separation(
    show:    ShowFile,
    config:  ValidationConfig,
    errors:  list,
    warnings: list,
) -> None:
    n    = len(show.trajectories)
    dur  = show.metadata.duration_s
    dt   = 1.0 / config.sample_hz
    times = np.arange(0.0, dur + dt * 0.5, dt)
    times = np.clip(times, 0.0, dur)

    for i in range(n):
        for j in range(i + 1, n):
            min_dist = math.inf
            min_t    = 0.0
            for t in times:
                d = distance_3d(
                    show.trajectories[i].evaluate(float(t)),
                    show.trajectories[j].evaluate(float(t)),
                )
                if d < min_dist:
                    min_dist = d
                    min_t    = float(t)
            if min_dist < config.min_sep_m:
                errors.append(
                    f"drones {i}&{j} minimum separation {min_dist:.3f}m < "
                    f"{config.min_sep_m}m at t={min_t:.1f}s"
                )
            elif min_dist < config.warn_close_m:
                warnings.append(
                    f"drones {i}&{j} come within {min_dist:.2f}m at t={min_t:.1f}s "
                    f"(warn threshold {config.warn_close_m}m)"
                )


def _check_speed(
    show:    ShowFile,
    config:  ValidationConfig,
    errors:  list,
    warnings: list,
) -> None:
    for traj in show.trajectories:
        for seg in traj.segments:
            n_samples = max(2, int((seg.t_end - seg.t_start) * config.sample_hz))
            for t in np.linspace(seg.t_start, seg.t_end, n_samples):
                vel   = seg.evaluate_velocity(float(t))
                speed = norm(vel)
                if speed > config.max_speed_ms:
                    warnings.append(
                        f"drone {traj.drone_id}: speed {speed:.1f} m/s > "
                        f"{config.max_speed_ms} m/s at t={t:.1f}s"
                    )
                    break   # one warning per segment is enough


def _check_reactive_bindings(show: ShowFile, errors: list, warnings: list) -> None:
    dur = show.metadata.duration_s
    for b in show.reactive_bindings:
        try:
            get_primitive(b.primitive)
        except KeyError:
            errors.append(f"reactive binding: unknown primitive '{b.primitive}'")
        if b.t_start < 0 or b.t_end > dur:
            errors.append(
                f"reactive binding '{b.primitive}': time [{b.t_start},{b.t_end}] "
                f"outside show [0,{dur}]"
            )
        if b.t_start >= b.t_end:
            errors.append(
                f"reactive binding '{b.primitive}': t_start={b.t_start} >= t_end={b.t_end}"
            )
        for did in b.drone_ids:
            if did < 0 or did >= show.metadata.n_drones:
                errors.append(
                    f"reactive binding '{b.primitive}': drone_id {did} out of range"
                )


def _check_led_tracks(show: ShowFile, errors: list, warnings: list) -> None:
    for track in show.led_tracks:
        if not track.keyframes:
            errors.append(f"drone {track.drone_id}: LED track has no keyframes")
            continue
        ts = [kf.t for kf in track.keyframes]
        if ts != sorted(ts):
            errors.append(f"drone {track.drone_id}: LED keyframes not sorted by time")


def _check_envelopes(show: ShowFile, errors: list, warnings: list) -> None:
    for env in show.envelopes:
        for seg in env.segments:
            if seg.radius_m < 0:
                errors.append(
                    f"drone {env.drone_id}: negative envelope radius {seg.radius_m:.3f}m"
                )
            if seg.t_start >= seg.t_end:
                errors.append(
                    f"drone {env.drone_id}: envelope segment t_start={seg.t_start} >= t_end={seg.t_end}"
                )
