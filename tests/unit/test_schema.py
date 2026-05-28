"""Tests for show format schema and serialisation round-trip."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from core.show_format.schema import (
    Color, DroneSpec, LedKeyframe, LedTrack,
    PolySegment, NominalTrajectory, Vec3,
)
from core.show_format.reader import from_json
from core.show_format.writer import to_json


def _make_minimal_show():
    from core.show_format.schema import (
        DroneEnvelope, EnvelopeSegment, ShowFile, ShowMetadata,
    )
    from compiler.show_builder import ShowBuilder, DRONES
    from shows.four_drone_demo import builder
    return builder.compile()


def test_vec3_arithmetic():
    a = Vec3(1, 2, 3)
    b = Vec3(4, 5, 6)
    assert (a + b) == Vec3(5, 7, 9)
    assert (b - a) == Vec3(3, 3, 3)


def test_poly_segment_evaluate():
    # p(t) = 1 + 2*dt + 3*dt^2 + dt^3, t_start=0, t_end=2
    seg = PolySegment(
        t_start=0.0, t_end=2.0,
        coeffs_n=[1.0, 2.0, 3.0, 1.0],
        coeffs_e=[0.0, 0.0, 0.0, 0.0],
        coeffs_d=[0.0, 0.0, 0.0, 0.0],
    )
    assert seg.evaluate(0.0).n == 1.0
    # at dt=1: 1 + 2 + 3 + 1 = 7
    assert abs(seg.evaluate(1.0).n - 7.0) < 1e-10
    # clamping: t > t_end → evaluate at t_end
    assert abs(seg.evaluate(10.0).n - seg.evaluate(2.0).n) < 1e-10


def test_led_track_interpolation():
    track = LedTrack(drone_id=0, keyframes=[
        LedKeyframe(0.0,  Color(0, 0, 0, 1)),
        LedKeyframe(10.0, Color(1, 1, 1, 1)),
    ])
    mid = track.evaluate(5.0)
    assert abs(mid.r - 0.5) < 1e-10
    assert abs(mid.g - 0.5) < 1e-10


def test_json_round_trip():
    from shows.four_drone_demo import builder
    show = builder.compile()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
    try:
        to_json(show, path)
        loaded = from_json(path)
        assert loaded.metadata.name == show.metadata.name
        assert loaded.metadata.n_drones == show.metadata.n_drones
        assert len(loaded.trajectories) == len(show.trajectories)
        assert len(loaded.led_tracks)   == len(show.led_tracks)
        # Check a trajectory evaluates the same
        t = 30.0
        for i in range(4):
            orig = show.trajectories[i].evaluate(t)
            load = loaded.trajectories[i].evaluate(t)
            assert abs(orig.n - load.n) < 1e-6
            assert abs(orig.e - load.e) < 1e-6
    finally:
        os.unlink(path)
