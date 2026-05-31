"""
Tests for the upload-and-go on-board agent's control law (OnboardAgent) and slice
loading. The pure core needs no MAVSDK — its setpoint at show_time must equal the
compiled trajectory's evaluation (identical to the player's nominal path).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

import pytest

from core.show_format.schema import (
    Color, DroneEnvelope, DroneSpec, EnvelopeSegment, LedKeyframe, LedTrack,
    NominalTrajectory, PolySegment, ShowFile, ShowMetadata, Vec3,
)
from agent.onboard_agent import OnboardAgent, load_slice, agent_conn


def _slice_show():
    # a 1-drone slice: moves N from 0→10 over [0,10] (linear), green LED.
    # all three coeff lists must be equal length (evaluate zips them); N = t, E = 2, D = -5
    seg = PolySegment(t_start=0.0, t_end=10.0,
                      coeffs_n=[0.0, 1.0], coeffs_e=[2.0, 0.0], coeffs_d=[-5.0, 0.0])
    return ShowFile(
        metadata=ShowMetadata(n_drones=1, duration_s=10.0, name="slice"),
        drones=[DroneSpec(logical_id=0, home_ned=Vec3())],
        trajectories=[NominalTrajectory(drone_id=0, segments=[seg])],
        led_tracks=[LedTrack(drone_id=0, keyframes=[LedKeyframe(0.0, Color(0.0, 0.8, 0.0))])],
        envelopes=[DroneEnvelope(drone_id=0, segments=[EnvelopeSegment(0.0, 10.0, 1.0)])],
        reactive_bindings=[],
    )


def test_position_matches_trajectory_evaluation():
    show = _slice_show()
    a = OnboardAgent(show)
    for t in (0.0, 3.0, 7.5, 10.0):
        v = show.trajectories[0].evaluate(t)
        assert a.position_at(t) == (v.n, v.e, v.d)
    # the linear segment: N = 0 + 1*t, E = 2 const, D = -5
    assert a.position_at(4.0) == (4.0, 2.0, -5.0)


def test_color_and_duration():
    a = OnboardAgent(_slice_show())
    assert a.duration == 10.0
    assert a.color_at(0.0) == (0.0, 0.8, 0.0)


def test_load_slice_rejects_multi_drone(tmp_path):
    from core.show_format.writer import to_json
    multi = ShowFile(
        metadata=ShowMetadata(n_drones=2, duration_s=5.0),
        drones=[DroneSpec(logical_id=i, home_ned=Vec3()) for i in range(2)],
        trajectories=[NominalTrajectory(drone_id=i, segments=[
            PolySegment(0.0, 5.0, [0.0], [0.0], [-5.0])]) for i in range(2)],
        led_tracks=[LedTrack(drone_id=i, keyframes=[LedKeyframe(0.0, Color())]) for i in range(2)],
        envelopes=[DroneEnvelope(drone_id=i, segments=[EnvelopeSegment(0.0, 5.0, 1.0)]) for i in range(2)],
        reactive_bindings=[],
    )
    p = tmp_path / "multi.skyforge.json"
    to_json(multi, str(p))
    with pytest.raises(ValueError):
        load_slice(str(p))


def test_agent_conn_targets_px4_instance():
    from show import config
    c = agent_conn(3)
    assert c.drone_id == 0                                    # indexed as drone 0 in its slice
    assert c.grpc_port == config.GRPC_BASE + 3                # but PX4 instance 3's ports
    assert str(config.MAVLINK_BASE + 3) in c.mavlink_url
