"""v_shape — a V pointing north (alias: v)."""
import math

from ..base import _centre, formation


@formation(aliases=("v",))
def v_shape(
    n: int,
    spacing_m: float = 2.0,
    half_angle_deg: float = 35.0,
) -> list[tuple[float, float]]:
    """V-shape pointing north; tip drone at origin for odd n."""
    a    = math.radians(half_angle_deg)
    half = n // 2
    tip  = [(0.0, 0.0)] if n % 2 == 1 else []
    left  = [(-k * spacing_m * math.sin(a),  k * spacing_m * math.cos(a)) for k in range(1, half + 1)]
    right = [(-k * spacing_m * math.sin(a), -k * spacing_m * math.cos(a)) for k in range(1, half + 1)]
    # _centre returns 3-tuples; code patterns honour the flat (dN, dE) contract
    # (get_formation re-adds dU=0). Keeps every generator's direct output 2-tuple.
    return [(p[0], p[1]) for p in _centre(tip + left + right)]
