"""Tests for compiler/validator.py."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from core.show_format.schema import (
    Color, DroneEnvelope, DroneSpec, EnvelopeSegment, LedKeyframe, LedTrack,
    NominalTrajectory, PolySegment, ReactiveBinding, ShowFile, ShowMetadata,
    Vec3, VenueOrigin,
)
from compiler.validator import ValidationConfig, validate


# ── Helpers ───────────────────────────────────────────────────────────────────

def _const_seg(t0, t1, n=0.0, e=0.0, d=0.0):
    return PolySegment(t_start=t0, t_end=t1,
                       coeffs_n=[n], coeffs_e=[e], coeffs_d=[d])


def _build_show(positions, duration=20.0):
    """Build a minimal show where each drone stays at a fixed NE position."""
    n = len(positions)
    trajs = [
        NominalTrajectory(
            drone_id=i,
            segments=[_const_seg(0.0, duration, pN, pE, -5.0)],
        )
        for i, (pN, pE) in enumerate(positions)
    ]
    return ShowFile(
        metadata=ShowMetadata(n_drones=n, duration_s=duration),
        drones=[DroneSpec(logical_id=i, home_ned=Vec3()) for i in range(n)],
        trajectories=trajs,
        led_tracks=[
            LedTrack(drone_id=i, keyframes=[LedKeyframe(0.0, Color())])
            for i in range(n)
        ],
        envelopes=[
            DroneEnvelope(drone_id=i, segments=[EnvelopeSegment(0.0, duration, 1.0)])
            for i in range(n)
        ],
        reactive_bindings=[],
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_valid_show_passes():
    """Well-separated drones should pass with no errors."""
    show   = _build_show([(0, 0), (0, 5), (5, 0), (5, 5)])
    result = validate(show, ValidationConfig(min_sep_m=1.5))
    assert result.passed, str(result)
    assert result.errors == []


def test_separation_violation_is_error():
    """Two drones at the same position must produce an error."""
    show   = _build_show([(0, 0), (0, 0)])
    result = validate(show, ValidationConfig(min_sep_m=1.5))
    assert not result.passed
    assert any("separation" in e.lower() or "0&1" in e for e in result.errors)


def test_bad_reactive_primitive_is_error():
    """An unknown reactive primitive name must produce an error."""
    show = _build_show([(0, 0), (0, 5)])
    show.reactive_bindings.append(ReactiveBinding(
        input_source="music_beat",
        primitive="fly_like_a_butterfly",   # not registered
        parameters={},
        t_start=0.0,
        t_end=10.0,
    ))
    result = validate(show)
    assert not result.passed
    assert any("fly_like_a_butterfly" in e for e in result.errors)


def test_tracking_margin_tightens_separation():
    """Drones 2 m apart pass at min_sep 1.5; with a 0.7 m tracking margin (required 2.2 m)
    the same show fails — the realism knob enforces physical min_sep under deviation."""
    show = _build_show([(0, 0), (0, 2.0)])
    assert validate(show, ValidationConfig(min_sep_m=1.5)).passed
    res = validate(show, ValidationConfig(min_sep_m=1.5, tracking_margin_m=0.7))
    assert not res.passed
    assert any("tracking margin" in e for e in res.errors)


def test_zero_margin_is_unchanged():
    show = _build_show([(0, 0), (0, 2.0)])
    assert validate(show, ValidationConfig(min_sep_m=1.5, tracking_margin_m=0.0)).passed


def test_temporal_gap_is_error():
    """A gap between trajectory segments must produce an error."""
    show = _build_show([(0, 0), (0, 5)])
    # Introduce a 0.5 s gap on drone 0
    show.trajectories[0].segments = [
        _const_seg(0.0,  9.0),
        _const_seg(9.5, 20.0),   # gap at 9.0–9.5
    ]
    result = validate(show)
    assert not result.passed
    assert any("gap" in e.lower() for e in result.errors)
