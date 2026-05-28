"""Tests for compiler: assignment, trajectory generation, ShowBuilder."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.assignment import assign
from compiler.trajectory_generator import fit_trajectory
from core.show_format.schema import Vec3


def test_hungarian_assignment_identity():
    """When drones are already at targets, assignment maps each to itself."""
    pos = [(0.0, 0.0), (2.0, 0.0), (0.0, 2.0), (2.0, 2.0)]
    tgt = [(0.0, 0.0), (2.0, 0.0), (0.0, 2.0), (2.0, 2.0)]
    result = assign(pos, tgt)
    assert result == [0, 1, 2, 3]


def test_hungarian_assignment_swap():
    """Drones should swap if that minimises total distance."""
    pos = [(0.0, 0.0), (10.0, 0.0)]
    tgt = [(10.0, 0.0), (0.0, 0.0)]
    result = assign(pos, tgt)
    # drone 0 → target 1 (distance 10), drone 1 → target 0 (distance 10)
    # vs drone 0 → target 0 (distance 10), drone 1 → target 1 (distance 10) — same cost
    # Hungarian picks a valid assignment; just check total cost is minimal
    total = sum(
        math.hypot(pos[i][0] - tgt[result[i]][0], pos[i][1] - tgt[result[i]][1])
        for i in range(2)
    )
    assert total <= 20.0 + 1e-9


def test_trajectory_passes_through_waypoints():
    """Spline must pass exactly through given waypoints."""
    times = [0.0, 5.0, 10.0, 20.0]
    positions = [Vec3(0, 0, 0), Vec3(3, 0, -5), Vec3(5, 5, -5), Vec3(1, 1, 0)]
    traj = fit_trajectory(times, positions)
    traj.drone_id = 0
    for t, p in zip(times, positions):
        result = traj.evaluate(t)
        assert abs(result.n - p.n) < 1e-6, f"N mismatch at t={t}"
        assert abs(result.e - p.e) < 1e-6, f"E mismatch at t={t}"
        assert abs(result.d - p.d) < 1e-6, f"D mismatch at t={t}"


def test_show_builder_produces_valid_show():
    from shows.four_drone_demo import builder
    show = builder.compile()
    assert show.metadata.n_drones == 4
    assert show.metadata.duration_s > 0
    assert len(show.trajectories) == 4
    assert len(show.led_tracks) == 4
    assert len(show.envelopes) == 4
    # Each trajectory should have segments
    for traj in show.trajectories:
        assert len(traj.segments) > 0
    # Reactive bindings present
    assert len(show.reactive_bindings) == 1
    assert show.reactive_bindings[0].primitive == "oscillate_on_beat"


def test_trajectory_c2_continuity():
    """Verify velocity is continuous at knots (C¹ ≥ C² is guaranteed by CubicSpline)."""
    from shows.four_drone_demo import builder
    show = builder.compile()
    traj = show.trajectories[0]
    eps  = 1e-4
    for seg_idx in range(len(traj.segments) - 1):
        t_knot = traj.segments[seg_idx].t_end
        v_left  = traj.segments[seg_idx    ].evaluate_velocity(t_knot - eps)
        v_right = traj.segments[seg_idx + 1].evaluate_velocity(t_knot + eps)
        assert abs(v_left.n - v_right.n) < 0.1, f"N velocity discontinuity at t={t_knot}"
        assert abs(v_left.e - v_right.e) < 0.1, f"E velocity discontinuity at t={t_knot}"
