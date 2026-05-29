"""Unit tests for the strengthened APF collision avoidance."""
import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show.apf import compute_apf_offset
from show.config import APF_D0, APF_MAX_OFFSET, APF_MAX_VERT, APF_MIN_SEP_M


def _call(own_ned, own_vel, others_ned, drone_id=0):
    return compute_apf_offset(own_ned, own_vel, others_ned, drone_id)


# ── Basic cases ───────────────────────────────────────────────────────────────

def test_no_neighbours_returns_zero_force():
    dN, dE, dD = _call((0, 0, -5), (0, 0, 0), [])
    assert dN == 0.0
    assert dD == 0.0
    # dE may have small asymmetric perturbation for drone_id=0 → 0.0


def test_force_points_away_from_neighbour():
    # Neighbour to the South (negative N); own drone at origin moving North
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(1.0, 0.0, 0.0),    # moving North (away from neighbour)
        others_ned=[(-2.0, 0.0, -5.0)],
    )
    # Closing speed is negative (moving apart) → no force expected
    assert dN == 0.0 or dN >= 0.0   # could be zero or tiny perturbation


def test_force_fires_when_approaching():
    # Neighbour to the South; own drone moving South (toward it)
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(-1.0, 0.0, 0.0),   # moving South = approaching
        others_ned=[(-2.0, 0.0, -5.0)],
    )
    # Repulsion should push North (positive dN)
    assert dN > 0.0


def test_no_force_outside_influence_radius():
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(-1.0, 0.0, 0.0),
        others_ned=[(-APF_D0 - 1.0, 0.0, -5.0)],  # just outside D0
    )
    assert dN == 0.0


def test_no_force_when_moving_apart():
    # Drones within influence radius but moving apart — no oscillation
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(2.0, 0.0, 0.0),    # moving North, away from southern neighbour
        others_ned=[(-1.5, 0.0, -5.0)],
    )
    # Only the asymmetric perturbation on dE for drone_id=0 (=0.0), no NE force
    assert dN == 0.0


# ── Emergency hold ────────────────────────────────────────────────────────────

def test_emergency_hold_below_min_sep():
    sep = APF_MIN_SEP_M * 0.5   # well inside the minimum
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(0.0, 0.0, 0.0),
        others_ned=[(- sep, 0.0, -5.0)],
    )
    total = math.hypot(dN, dE)
    assert total >= APF_MAX_OFFSET * 0.99   # clamped to max


def test_emergency_hold_ignores_velocity_direction():
    sep = APF_MIN_SEP_M * 0.5
    # Even if moving away, emergency hold must still fire
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(5.0, 0.0, 0.0),    # fast, moving away from Southern neighbour
        others_ned=[(-sep, 0.0, -5.0)],
    )
    total = math.hypot(dN, dE)
    assert total >= APF_MAX_OFFSET * 0.99


def test_emergency_aggregates_all_neighbours():
    """Emergency repulsion combines EVERY too-close neighbour, not just the first
    one — a drone pinned to its south and west escapes north-east."""
    sep = APF_MIN_SEP_M * 0.6
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(0.0, 0.0, 0.0),
        others_ned=[(-sep, 0.0, -5.0), (0.0, -sep, -5.0)],   # south + west
    )
    assert dN > 0.0 and dE > 0.0                              # pushed away from both
    assert math.hypot(dN, dE) >= APF_MAX_OFFSET * 0.99        # clamped to max


def test_emergency_applies_horizontal_and_vertical_escape_together():
    """When a drone is hemmed in by an offset neighbour AND an NE-collocated one,
    BOTH escapes fire (the vertical one used to be dropped)."""
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(0.0, 0.0, 0.0),
        others_ned=[
            (APF_MIN_SEP_M * 0.6, 0.0, -5.0),   # offset to the north → horizontal escape
            (0.0, 0.0, -5.0 - APF_MIN_SEP_M * 0.6),   # directly above (NE-collocated) → vertical escape
        ],
    )
    assert math.hypot(dN, dE) > 0.0                 # horizontal escape present
    assert abs(dD) >= APF_MAX_VERT * 0.99           # vertical escape NOT dropped


# ── Vertical repulsion ────────────────────────────────────────────────────────

def test_vertical_repulsion_fires():
    # Drones 2 m apart in NE (well outside emergency hold), 1 m vertical separation
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(0.0, 0.0, -0.5),   # moving down toward neighbour below
        others_ned=[(2.0, 0.0, -6.0)],
    )
    # Repulsion should push upward in NED (positive dD = away from lower neighbour)
    assert dD > 0.0


def test_no_vertical_force_when_moving_apart():
    # 2 m NE separation keeps 3D distance above APF_MIN_SEP_M so emergency hold is off
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(0.0, 0.0, 0.5),    # moving up, away from lower neighbour
        others_ned=[(2.0, 0.0, -6.0)],
    )
    assert dD == 0.0


# ── Clamping ──────────────────────────────────────────────────────────────────

def test_ne_clamp_respected():
    # Pack many neighbours to create large raw force
    neighbours = [(-1.5 + i * 0.01, 0.0, -5.0) for i in range(10)]
    dN, dE, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(-2.0, 0.0, 0.0),
        others_ned=neighbours,
        drone_id=0,
    )
    assert math.hypot(dN, dE) <= APF_MAX_OFFSET + 1e-9


def test_vertical_clamp_respected():
    neighbours = [(0.0, 0.0, -5.0 + 0.5 * i) for i in range(10)]
    _, _, dD = _call(
        own_ned=(0.0, 0.0, -5.0),
        own_vel=(0.0, 0.0, -2.0),
        others_ned=neighbours,
    )
    assert abs(dD) <= APF_MAX_VERT + 1e-9


# ── Asymmetric perturbation ───────────────────────────────────────────────────

def test_asymmetric_perturbation_breaks_symmetry():
    # Two drones heading directly at each other along N axis
    # Drone 0 moves south, drone 1 moves north — without perturbation forces cancel
    # With perturbation, they should get non-identical offsets
    dN0, dE0, _ = compute_apf_offset(
        (0.0, 0.0, -5.0), (-1.0, 0.0, 0.0),
        [(- 2.0, 0.0, -5.0)], drone_id=0
    )
    dN1, dE1, _ = compute_apf_offset(
        (-2.0, 0.0, -5.0), (1.0, 0.0, 0.0),
        [(0.0, 0.0, -5.0)], drone_id=1
    )
    # Perturbation is on dN (drone_id * 0.05); drone 0 gets +0, drone 1 gets +0.05
    # Their dN values will differ by more than just the perturbation
    assert abs(dN0 - dN1) > 0.0
