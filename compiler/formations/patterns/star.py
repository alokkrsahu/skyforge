"""star — drones on a star polygon."""
import math

from ..base import _pad_to, formation


@formation
def star(
    n: int,
    n_points: int = 5,
    r_outer: float = 6.0,
    r_inner: float = 3.0,
) -> list[tuple[float, float]]:
    """Drones on a star polygon with n_points arms, padded/subsampled to n."""
    verts: list[tuple[float, float]] = []
    for k in range(2 * n_points):
        r     = r_outer if k % 2 == 0 else r_inner
        angle = math.pi / 2 - k * math.pi / n_points
        verts.append((r * math.cos(angle), r * math.sin(angle)))
    return _pad_to(verts, n)
