"""
Vectorised trajectory sampling.

The scalar ``NominalTrajectory.evaluate`` does a linear segment scan per call, so
sampling n drones at T times costs O(n·T·segments) Python calls — and the pairwise
separation / envelope / deconfliction passes layer an extra O(n²) on top, which
becomes intractable at 100 drones (a single 20 Hz separation scan took ~4 s).

``sample_positions`` evaluates every trajectory at every time with NumPy, matching
``NominalTrajectory.evaluate`` / ``PolySegment.evaluate`` semantics EXACTLY
(including the dt clamp and the before-first / after-last boundary behaviour), so it
is a drop-in replacement that returns an ``(n, T, 3)`` array of NED positions.
"""
from __future__ import annotations

import numpy as np

from core.show_format.schema import NominalTrajectory


def _sample_one(traj: NominalTrajectory, times: np.ndarray) -> np.ndarray:
    """Evaluate a single trajectory at ``times`` → ``(T, 3)`` array (N, E, D)."""
    segs = traj.segments
    T = len(times)
    if not segs:
        return np.zeros((T, 3))

    starts = np.fromiter((s.t_start for s in segs), dtype=float, count=len(segs))
    ends   = np.fromiter((s.t_end   for s in segs), dtype=float, count=len(segs))

    # Segment index per time: the last segment whose t_start <= t (clamped to range).
    # 'right' + (-1) means a t exactly on a boundary picks the later segment, which
    # is value-identical to the earlier one (trajectories are C0-continuous) — and
    # matches evaluate()'s before-first (→seg0 @ dt=0) / after-last (→last @ dt=len)
    # behaviour once dt is clamped below.
    idx = np.clip(np.searchsorted(starts, times, side="right") - 1, 0, len(segs) - 1)

    seg_start = starts[idx]
    seg_len   = ends[idx] - seg_start
    dt = np.clip(times - seg_start, 0.0, seg_len)          # matches PolySegment.evaluate

    # Coefficient matrices, padded to the max degree across segments (cubic = 4).
    maxk = max(len(s.coeffs_n) for s in segs)
    cn = np.zeros((len(segs), maxk)); ce = np.zeros((len(segs), maxk)); cd = np.zeros((len(segs), maxk))
    for j, s in enumerate(segs):
        cn[j, :len(s.coeffs_n)] = s.coeffs_n
        ce[j, :len(s.coeffs_e)] = s.coeffs_e
        cd[j, :len(s.coeffs_d)] = s.coeffs_d

    powers = np.vander(dt, maxk, increasing=True)          # [dt^0, dt^1, ..., dt^(maxk-1)]
    n_ = np.einsum("tk,tk->t", cn[idx], powers)
    e_ = np.einsum("tk,tk->t", ce[idx], powers)
    d_ = np.einsum("tk,tk->t", cd[idx], powers)
    return np.stack([n_, e_, d_], axis=1)


def sample_positions(trajectories, times) -> np.ndarray:
    """
    Evaluate every trajectory at every time. Returns ``(n, T, 3)`` of NED metres.

    ``times`` may be any 1-D sequence/array. Values outside a trajectory's
    [0, duration] range are handled exactly as ``NominalTrajectory.evaluate`` does.
    """
    times = np.asarray(times, dtype=float)
    n = len(trajectories)
    out = np.empty((n, len(times), 3))
    for i, traj in enumerate(trajectories):
        out[i] = _sample_one(traj, times)
    return out
