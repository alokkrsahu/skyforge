"""spiral — Archimedean spiral from centre outward, arc-length even."""
import numpy as np

from ..base import formation


@formation
def spiral(
    n: int,
    turns: float = 2.0,
    r_max: float = 8.0,
) -> list[tuple[float, float]]:
    """
    Archimedean spiral from centre outward, with drones spaced evenly by ARC
    LENGTH rather than by linear radius. The naive r = r_max·k/(n-1) form bunches
    drones tightly near the centre (sub-centimetre gaps at 100 drones); arc-length
    resampling gives uniform gaps along the curve, so a later min-spacing scale-up
    only has to enlarge by a small, sane factor instead of ~20×.
    """
    if n <= 1:
        return [(0.0, 0.0)]
    # Densely trace the spiral, then resample n points at equal cumulative arc length.
    m     = max(2000, n * 20)
    theta = np.linspace(0.0, 2 * np.pi * turns, m)
    r     = r_max * (theta / (2 * np.pi * turns))
    ang   = np.pi / 2 + theta
    x = r * np.cos(ang)
    y = r * np.sin(ang)
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    targets = np.linspace(0.0, s[-1], n)
    xi = np.interp(targets, s, x)
    yi = np.interp(targets, s, y)
    return list(zip(xi.tolist(), yi.tolist()))
