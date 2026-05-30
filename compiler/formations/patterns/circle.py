"""circle — N drones equally spaced on a ring."""
import math

from ..base import formation


@formation
def circle(n: int, radius_m: float = 5.0) -> list[tuple[float, float]]:
    """N drones equally spaced on a circle, first drone at due north."""
    return [
        (radius_m * math.cos(math.pi / 2 - k * 2 * math.pi / n),
         radius_m * math.sin(math.pi / 2 - k * 2 * math.pi / n))
        for k in range(n)
    ]
