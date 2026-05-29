"""
Convert a sequence of formation waypoints into piecewise polynomial trajectories.
Uses cubic splines (C² continuity) per the TRD v1 spec.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import CubicHermiteSpline

from core.show_format.schema import NominalTrajectory, PolySegment, Vec3


def _hold_aware_tangents(t: np.ndarray, pts: np.ndarray, hold_tol: float = 1e-6) -> np.ndarray:
    """
    Per-knot velocity for a Hermite fit. Zero at the endpoints (drone at rest on
    the ground) and at any HOLD knot — a waypoint whose position equals an adjacent
    one, i.e. the drone is parked in a formation. Elsewhere use a centred
    (Catmull-Rom) tangent for smooth motion.

    A plain natural cubic spline carries momentum THROUGH the hold knots, so drones
    overshoot their formation slots and drift into each other during a "hold"
    (observed: a 100-drone text formation dipping to 0.02 m). Pinning velocity to
    zero at holds makes them actually stop, so the held formation keeps its spacing.
    """
    m = len(t)
    d = np.zeros_like(pts)
    for k in range(m):
        is_hold = (
            (k > 0     and np.allclose(pts[k], pts[k - 1], rtol=0.0, atol=hold_tol)) or
            (k < m - 1 and np.allclose(pts[k], pts[k + 1], rtol=0.0, atol=hold_tol))
        )
        if k == 0 or k == m - 1 or is_hold:
            d[k] = 0.0                                   # at rest
        else:
            d[k] = (pts[k + 1] - pts[k - 1]) / (t[k + 1] - t[k - 1])   # Catmull-Rom
    return d


def fit_trajectory(
    times:     list[float],
    positions: list[Vec3],
) -> NominalTrajectory:
    """
    Fit a piecewise cubic Hermite spline through (time, position) waypoints.
    Returns a NominalTrajectory with per-segment polynomial coefficients.

    Uses hold-aware tangents (zero velocity at the ends and at formation holds)
    so drones genuinely stop in formation instead of overshooting through their
    neighbours — see _hold_aware_tangents.

    times:     monotonically increasing show timestamps (seconds)
    positions: Vec3 positions at each timestamp in global NED
    """
    assert len(times) == len(positions), "times and positions must match"
    assert len(times) >= 2, "need at least 2 waypoints"

    # Build Nx3 position array
    pts = np.array([[p.n, p.e, p.d] for p in positions])
    t   = np.array(times)

    # Cubic Hermite with hold-aware tangents (C¹; firm stops at holds/ends).
    cs = CubicHermiteSpline(t, pts, _hold_aware_tangents(t, pts))

    # cs.c has shape (4, n_segments, 3)
    # cs.c[k, i, a] = coefficient of (t - t_i)^(3-k) for axis a on segment i
    # We want INCREASING power order: coeffs[k] multiplies (dt)^k
    # scipy stores DECREASING: cs.c[0] = dt^3, cs.c[1] = dt^2, cs.c[2] = dt^1, cs.c[3] = dt^0
    # So reverse along the first axis.

    n_segs = len(times) - 1
    segments = []
    for i in range(n_segs):
        # scipy coefficients in decreasing order → reverse to increasing
        coeffs = cs.c[:, i, :]   # shape (4, 3)
        coeffs_inc = coeffs[::-1]  # now coeffs_inc[0] = constant, ..., coeffs_inc[3] = dt^3
        segments.append(PolySegment(
            t_start  = float(t[i]),
            t_end    = float(t[i + 1]),
            coeffs_n = coeffs_inc[:, 0].tolist(),
            coeffs_e = coeffs_inc[:, 1].tolist(),
            coeffs_d = coeffs_inc[:, 2].tolist(),
        ))

    traj = NominalTrajectory(drone_id=-1, segments=segments)
    return traj
