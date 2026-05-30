"""arrow — legacy 4-drone arrowhead (padded/subsampled for n != 4)."""
from ..base import _pad_to, formation

_PTS = [(0.0, 0.0), (2.0, -2.0), (2.0, 2.0), (4.0, 0.0)]


@formation
def arrow(n: int) -> list[tuple[float, float]]:
    """Legacy 4-drone arrowhead, padded/subsampled to n."""
    return _pad_to(_PTS, n)
