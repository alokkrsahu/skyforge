"""Cubic Bézier path segment evaluated live each control tick."""
import math
from dataclasses import dataclass
from typing import Tuple


def _cubic_bezier(p0, p1, p2, p3, t):
    mt = 1.0 - t
    return (
        mt**3 * p0[0] + 3*mt**2*t * p1[0] + 3*mt*t**2 * p2[0] + t**3 * p3[0],
        mt**3 * p0[1] + 3*mt**2*t * p1[1] + 3*mt*t**2 * p2[1] + t**3 * p3[1],
    )


@dataclass
class BezierSegment:
    """Smooth arc from start_ne to end_ne over duration_s seconds."""
    start_ne:   Tuple[float, float]
    end_ne:     Tuple[float, float]
    duration_s: float

    def __post_init__(self):
        p0 = self.start_ne
        p3 = self.end_ne
        dN = p3[0] - p0[0]
        dE = p3[1] - p0[1]
        dist = math.hypot(dN, dE) or 1.0
        # Perpendicular unit vector (rotate 90°)
        perp = (-dE / dist, dN / dist)
        arc  = 1.0  # metres of lateral arc
        self._p1 = (p0[0] + dN/3 + perp[0]*arc, p0[1] + dE/3 + perp[1]*arc)
        self._p2 = (p0[0] + 2*dN/3 - perp[0]*arc, p0[1] + 2*dE/3 - perp[1]*arc)

    def position_at(self, elapsed_s: float) -> Tuple[float, float]:
        """Return (N, E) on the curve; clamps t to [0, 1]."""
        t = max(0.0, min(1.0, elapsed_s / self.duration_s))
        return _cubic_bezier(self.start_ne, self._p1, self._p2, self.end_ne, t)
