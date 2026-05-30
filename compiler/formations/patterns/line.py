"""line — N drones in an E-W line."""
from ..base import formation


@formation
def line(n: int, spacing_m: float = 2.0) -> list[tuple[float, float]]:
    """N drones in an E-W line, centred on origin."""
    return [(0.0, (k - (n - 1) / 2.0) * spacing_m) for k in range(n)]
