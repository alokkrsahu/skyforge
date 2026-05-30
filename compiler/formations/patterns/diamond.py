"""diamond — legacy 4-point diamond (padded/subsampled for n != 4)."""
from ..base import _pad_to, formation

_PTS = [(-2.0, 0.0), (0.0, -2.0), (2.0, 0.0), (0.0, 2.0)]


@formation
def diamond(n: int) -> list[tuple[float, float]]:
    """Legacy 4-point diamond, padded/subsampled to n."""
    return _pad_to(_PTS, n)
