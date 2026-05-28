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


def assign_nocross(
    current_positions: list[tuple[float, float]],
    target_positions:  list[tuple[float, float]],
) -> list[int]:
    """
    Hungarian assignment refined by greedy first-crossing swaps.

    Finds the first pair (i, j) whose paths cross and swaps their targets
    immediately.  Repeats until no crossings remain or the iteration cap
    (min(n², 300)) is hit.  O(n²) per iteration — fast even for n=100
    because well-designed formations start with very few crossings.
    """
    assignment = assign(current_positions, target_positions)
    n   = len(assignment)
    cap = min(n * n, 300)

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

    return assignment
