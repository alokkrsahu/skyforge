"""
Safety envelope computation  (Phase 2).

For each drone at each time sample the maximum allowable position deviation is:

    radius_i(t) = min_{j≠i}  max(0,  (dist_3d(traj_i(t), traj_j(t)) - min_sep_m) / 2 )

The /2 is the symmetric worst-case: both drone i and drone j could deviate
toward each other simultaneously.

Consecutive samples with similar radii are merged into piecewise-constant
EnvelopeSegment objects (conservative: each segment stores the minimum radius
observed within its window).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from core.geometry import distance_3d
from core.show_format.schema import DroneEnvelope, EnvelopeSegment, ShowFile
from compiler.sampling import sample_positions


@dataclass
class EnvelopeConfig:
    min_sep_m:    float = 1.5    # required clearance between any two drones (m)
    sample_hz:    float = 10.0   # trajectory sampling rate for computation
    merge_tol_m:  float = 0.05   # merge adjacent segments if radius differs < this
    max_radius_m: float = 5.0    # cap for single-drone shows (no neighbours)


def compute_envelopes(
    show: ShowFile,
    config: EnvelopeConfig | None = None,
) -> list[DroneEnvelope]:
    """
    Compute per-drone safety envelopes from the nominal trajectories in *show*.
    Returns a list of DroneEnvelope, one per drone, in drone_id order.
    """
    if config is None:
        config = EnvelopeConfig()

    n         = len(show.trajectories)
    duration  = show.metadata.duration_s
    dt        = 1.0 / config.sample_hz

    # Build sample times that exactly include duration_s
    times = np.arange(0.0, duration + dt * 0.5, dt)
    times = np.clip(times, 0.0, duration)

    # Sample every trajectory once (vectorised): shape (n, T, 3)
    positions = sample_positions(show.trajectories, times)

    envelopes: list[DroneEnvelope] = []
    for i in range(n):
        # 3-D distance from drone i to every other drone at every time → (n, T)
        dists = np.linalg.norm(positions - positions[i][None, :, :], axis=2)
        dists[i] = np.inf                                    # ignore self
        # radius_i(t) = min_j max(0, (dist - min_sep)/2), capped; no neighbours → max
        r = np.maximum(0.0, (dists - config.min_sep_m) / 2.0)
        min_r = r.min(axis=0)                                # (T,) min over j
        min_r = np.where(
            np.isfinite(min_r),
            np.minimum(config.max_radius_m, min_r),
            config.max_radius_m,
        )
        segments = _merge_to_segments(times, min_r.tolist(), duration, config.merge_tol_m)
        envelopes.append(DroneEnvelope(drone_id=i, segments=segments))

    return envelopes


def _merge_to_segments(
    times: np.ndarray,
    radii: list[float],
    duration_s: float,
    merge_tol: float,
) -> list[EnvelopeSegment]:
    """
    Greedy merge: extend the current segment as long as no sample drops
    more than *merge_tol* below the segment's opening value.
    Each segment stores the minimum observed radius (conservative).
    """
    segments: list[EnvelopeSegment] = []
    s = 0
    n = len(times)

    while s < n:
        opening    = radii[s]
        running_min = radii[s]
        j = s

        while j + 1 < n:
            candidate_min = min(running_min, radii[j + 1])
            if opening - candidate_min > merge_tol:
                break
            running_min = candidate_min
            j += 1

        t_start = float(times[s])
        t_end   = float(times[j + 1]) if j + 1 < n else duration_s
        segments.append(EnvelopeSegment(
            t_start  = t_start,
            t_end    = t_end,
            radius_m = running_min,
        ))
        s = j + 1

    return segments
