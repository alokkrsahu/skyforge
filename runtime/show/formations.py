"""Virtual Structure formation helpers."""
from typing import List, Tuple

from .config import FORMATIONS, SHOW_ALT_M


def formation_targets(
    name: str,
    center_ne: Tuple[float, float],
    altitude_m: float = SHOW_ALT_M,
) -> List[Tuple[float, float, float]]:
    """
    Returns list of 4 (global_N, global_E, down_m) targets.
    Drone i is assigned offsets[i] — fixed index assignment.
    """
    cN, cE = center_ne
    down   = -altitude_m
    return [(cN + dN, cE + dE, down) for (dN, dE) in FORMATIONS[name]]
