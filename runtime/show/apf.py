"""Artificial Potential Field repulsion — returns a position-domain offset."""
import math
from typing import List, Tuple

from .config import APF_D0, APF_K, APF_MAX_OFFSET


def compute_apf_offset(
    own_ne: Tuple[float, float],
    others_ne: List[Tuple[float, float]],
    drone_id: int,
    d0: float = APF_D0,
    k: float = APF_K,
) -> Tuple[float, float]:
    """
    Returns (dN, dE) repulsion offset to add to the nominal position setpoint.

    The tiny drone_id perturbation on dE breaks head-on symmetry so two drones
    approaching each other never get a net-zero force.
    """
    total_dN = 0.0
    total_dE = drone_id * 0.01   # asymmetric perturbation

    for other in others_ne:
        dN = own_ne[0] - other[0]
        dE = own_ne[1] - other[1]
        d  = math.hypot(dN, dE)
        if 0.01 < d < d0:
            mag = k * (1.0/d - 1.0/d0) / (d * d)
            total_dN += mag * (dN / d)
            total_dE += mag * (dE / d)

    # Clamp to prevent runaway on very close approach
    total = math.hypot(total_dN, total_dE)
    if total > APF_MAX_OFFSET:
        scale   = APF_MAX_OFFSET / total
        total_dN *= scale
        total_dE *= scale

    return (total_dN, total_dE)
