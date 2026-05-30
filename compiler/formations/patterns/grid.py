"""grid — rectangular grid, as square as possible."""
import math

from ..base import formation


@formation
def grid(n: int, cols: int | None = None, spacing_m: float = 2.0) -> list[tuple[float, float]]:
    """Rectangular grid, as square as possible when cols is not given."""
    if cols is None:
        cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    pts = [
        ((r - (rows - 1) / 2.0) * spacing_m, (c - (cols - 1) / 2.0) * spacing_m)
        for r in range(rows)
        for c in range(cols)
    ]
    return pts[:n]
