"""
Convert a sequence of formation waypoints into piecewise polynomial trajectories.
Uses cubic splines (C² continuity) per the TRD v1 spec.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import CubicSpline

from core.show_format.schema import NominalTrajectory, PolySegment, Vec3


def fit_trajectory(
    times:     list[float],
    positions: list[Vec3],
) -> NominalTrajectory:
    """
    Fit a piecewise cubic spline through (time, position) waypoints.
    Returns a NominalTrajectory with per-segment polynomial coefficients.

    times:     monotonically increasing show timestamps (seconds)
    positions: Vec3 positions at each timestamp in global NED
    """
    assert len(times) == len(positions), "times and positions must match"
    assert len(times) >= 2, "need at least 2 waypoints"

    # Build Nx3 position array
    pts = np.array([[p.n, p.e, p.d] for p in positions])
    t   = np.array(times)

    # Fit natural cubic spline (C² at interior knots, zero-curvature at ends)
    cs = CubicSpline(t, pts, bc_type="natural")

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
