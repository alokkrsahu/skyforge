"""Tests for compiler/envelope.py — safety envelope computation."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import math
import pytest

from core.show_format.schema import (
    DroneSpec, EnvelopeSegment, NominalTrajectory, PolySegment,
    ShowFile, ShowMetadata, Vec3, DroneEnvelope, LedTrack, LedKeyframe,
    Color, ReactiveBinding, VenueOrigin,
)
from compiler.envelope import EnvelopeConfig, compute_envelopes


def _constant_traj(drone_id: int, n: float, e: float, d: float, duration: float) -> NominalTrajectory:
    """Trajectory that stays at a fixed point for the entire show."""
    seg = PolySegment(
        t_start=0.0, t_end=duration,
        coeffs_n=[n], coeffs_e=[e], coeffs_d=[d],
    )
    return NominalTrajectory(drone_id=drone_id, segments=[seg])


def _minimal_show(trajs, duration=10.0):
    n = len(trajs)
    for i, t in enumerate(trajs):
        t.drone_id = i
    return ShowFile(
        metadata=ShowMetadata(n_drones=n, duration_s=duration),
        drones=[DroneSpec(logical_id=i, home_ned=Vec3()) for i in range(n)],
        trajectories=trajs,
        led_tracks=[LedTrack(drone_id=i, keyframes=[LedKeyframe(0.0, Color())]) for i in range(n)],
        envelopes=[DroneEnvelope(drone_id=i, segments=[EnvelopeSegment(0.0, duration, 0.0)]) for i in range(n)],
        reactive_bindings=[],
    )


def test_single_drone_gets_max_radius():
    """One drone with no neighbours → radius capped at max_radius_m."""
    show = _minimal_show([_constant_traj(0, 0, 0, 0, 10.0)])
    cfg  = EnvelopeConfig(max_radius_m=3.0)
    envs = compute_envelopes(show, cfg)
    assert len(envs) == 1
    for seg in envs[0].segments:
        assert abs(seg.radius_m - 3.0) < 1e-6


def test_two_drones_same_position_zero_radius():
    """Two drones at identical positions → zero envelope radius."""
    show = _minimal_show([
        _constant_traj(0, 0, 0, 0, 10.0),
        _constant_traj(1, 0, 0, 0, 10.0),
    ])
    envs = compute_envelopes(show, EnvelopeConfig(min_sep_m=1.5))
    for env in envs:
        for seg in env.segments:
            assert seg.radius_m == 0.0


def test_two_drones_known_separation():
    """Two drones 6 m apart → radius = (6 - 1.5) / 2 = 2.25 m."""
    show = _minimal_show([
        _constant_traj(0, 0.0, 0.0, 0.0, 10.0),
        _constant_traj(1, 6.0, 0.0, 0.0, 10.0),
    ])
    envs = compute_envelopes(show, EnvelopeConfig(min_sep_m=1.5))
    for env in envs:
        for seg in env.segments:
            assert abs(seg.radius_m - 2.25) < 1e-6


def test_demo_show_all_positive_radii():
    """four_drone_demo compiled show has positive radii throughout."""
    from shows.four_drone_demo import builder
    from compiler.pipeline import CompilePipeline, CompileConfig
    from compiler.envelope import EnvelopeConfig

    result = CompilePipeline(CompileConfig(validate=False)).run(builder)
    for env in result.show.envelopes:
        for seg in env.segments:
            assert seg.radius_m >= 0.0, f"negative radius on drone {env.drone_id}"
