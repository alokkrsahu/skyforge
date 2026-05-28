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
