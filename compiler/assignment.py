"""
Hungarian algorithm for optimal drone-to-formation-position assignment.
Minimises total path length across all drones.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.optimize import linear_sum_assignment


def assign(
    current_positions: list[tuple[float, float]],
    target_positions:  list[tuple[float, float]],
) -> list[int]:
    """
    Returns assignment[i] = j meaning drone i should fly to target j.
    Minimises sum of Euclidean distances.

    current_positions: list of (N, E) for each drone
    target_positions:  list of (N, E) for each target slot
    """
    n = len(current_positions)
    m = len(target_positions)
    assert n == m, f"Drone count {n} != target count {m}"

    cost = np.zeros((n, m))
    for i, (cN, cE) in enumerate(current_positions):
        for j, (tN, tE) in enumerate(target_positions):
            cost[i, j] = math.hypot(cN - tN, cE - tE)

    row_ind, col_ind = linear_sum_assignment(cost)
    assignment = [0] * n
    for i, j in zip(row_ind, col_ind):
        assignment[i] = j
    return assignment


def _segments_cross(
    p0: tuple[float, float], p1: tuple[float, float],
    q0: tuple[float, float], q1: tuple[float, float],
) -> bool:
    """True if line segments p0→p1 and q0→q1 properly intersect (not at endpoints)."""
    def cross2d(a: tuple[float, float], b: tuple[float, float]) -> float:
        return a[0] * b[1] - a[1] * b[0]

    r = (p1[0] - p0[0], p1[1] - p0[1])
    s = (q1[0] - q0[0], q1[1] - q0[1])
    rxs = cross2d(r, s)
    if abs(rxs) < 1e-10:
        return False  # parallel or collinear
    d = (q0[0] - p0[0], q0[1] - p0[1])
    t = cross2d(d, s) / rxs
    u = cross2d(d, r) / rxs
    return 0.0 < t < 1.0 and 0.0 < u < 1.0


def _count_crossings(
    current: list[tuple[float, float]],
    targets: list[tuple[float, float]],
    assignment: list[int],
) -> int:
    n = len(assignment)
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            if _segments_cross(
                current[i], targets[assignment[i]],
                current[j], targets[assignment[j]],
            ):
                count += 1
    return count


def _min_separation(
    ci: tuple[float, float], ti: tuple[float, float],
    cj: tuple[float, float], tj: tuple[float, float],
) -> float:
    """
    Closest approach (m) between two drones moving linearly current→target over
    the SAME normalised transition time [0, 1] (the show advances all drones in
    lock-step). Closed form: |A + sB| minimised over s∈[0,1].

    Unlike _segments_cross this catches collinear / same-direction-different-speed
    paths that pass through each other without a geometric "X" intersection.
    """
    ax, ay = ci[0] - cj[0], ci[1] - cj[1]                       # relative start
    bx = (ti[0] - ci[0]) - (tj[0] - cj[0])                      # relative velocity
    by = (ti[1] - ci[1]) - (tj[1] - cj[1])
    bb = bx * bx + by * by
    if bb < 1e-12:
        s = 0.0                                                 # no relative motion
    else:
        s = max(0.0, min(1.0, -(ax * bx + ay * by) / bb))
    return math.hypot(ax + s * bx, ay + s * by)


def assign_nocross(
    current_positions: list[tuple[float, float]],
    target_positions:  list[tuple[float, float]],
    min_sep_m:         float = 1.5,
) -> list[int]:
    """
    Hungarian assignment refined by greedy swaps so paths neither cross nor pass
    too close.

    Phase A — swap the first pair whose straight paths geometrically cross.
    Phase B — swap the worst pair whose *time-parameterised* closest approach is
              below min_sep_m (catches collinear / same-line collisions Phase A's
              segment test misses — e.g. (4,4)→(2,2) vs (6,6)→(0,0), which collide
              at (3,3)). Each accepted swap strictly increases that pair's
              clearance; if the worst pair can't be improved by swapping its own
              targets we stop (validation then flags any residual) — this also
              guarantees termination. O(n²) per iteration, cap min(n², 300).
    """
    assignment = assign(current_positions, target_positions)
    n   = len(assignment)
    cap = min(n * n, 300)

    # ── Phase A: eliminate geometric crossings ────────────────────────────────
    for _ in range(cap):
        found = False
        for i in range(n):
            for j in range(i + 1, n):
                if _segments_cross(
                    current_positions[i], target_positions[assignment[i]],
                    current_positions[j], target_positions[assignment[j]],
                ):
                    assignment[i], assignment[j] = assignment[j], assignment[i]
                    found = True
                    break
            if found:
                break
        if not found:
            break

    # ── Phase B: enforce time-parameterised separation ────────────────────────
    for _ in range(cap):
        worst_sep = min_sep_m
        wi = wj = -1
        for i in range(n):
            for j in range(i + 1, n):
                sep = _min_separation(
                    current_positions[i], target_positions[assignment[i]],
                    current_positions[j], target_positions[assignment[j]],
                )
                if sep < worst_sep:
                    worst_sep, wi, wj = sep, i, j
        if wi < 0:
            break   # every pair clears min_sep_m

        assignment[wi], assignment[wj] = assignment[wj], assignment[wi]
        after = _min_separation(
            current_positions[wi], target_positions[assignment[wi]],
            current_positions[wj], target_positions[assignment[wj]],
        )
        if after <= worst_sep:
            # Swapping the worst pair's targets did not help — revert and stop.
            assignment[wi], assignment[wj] = assignment[wj], assignment[wi]
            break

    return assignment
