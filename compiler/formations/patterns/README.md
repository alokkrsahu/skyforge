# Formation patterns

One file per pattern. **Drop a file here and it's instantly a usable formation** —
no edits to any other file. The folder *is* the catalog: `list_formations()` scans it,
the commander's `formations` command lists it, and `get_formation("<name>", n)` resolves
it (lazily — only the requested pattern is imported, so this scales to thousands of files).

The pattern **name is the file name** (lowercase recommended; the spec is matched
case-insensitively). Files starting with `_` or `.` are ignored.

## Two kinds of pattern

### 1. Code pattern — `patterns/<name>.py` (parametric)
A function returning a list of `(dN, dE)` offsets in **metres**, centred near the origin,
for the given fleet size `n`. Optional keyword params are passable via the spec string
(`"<name>:radius_m=8"`), filtered to the function's signature.

```python
# patterns/heart.py
import math
from ..base import formation          # _centre / _pad_to also available here

@formation                            # or: @formation(aliases=("love",), description="…")
def heart(n: int, scale_m: float = 4.0) -> list[tuple[float, float]]:
    """A heart curve, n drones along it."""
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        e = 16 * math.sin(t) ** 3
        nN = 13 * math.cos(t) - 5 * math.cos(2*t) - 2 * math.cos(3*t) - math.cos(4*t)
        pts.append((nN * scale_m / 16, e * scale_m / 16))
    return pts
```

Use `from ..base import formation, _centre, _pad_to` for the shared helpers
(`_centre` re-centres on the origin; `_pad_to(pts, n)` subsamples/pads a fixed-size
shape to exactly `n`).

### 2. Data pattern — `patterns/<name>.csv` or `patterns/<name>.json` (designed point-cloud)
A fixed set of `(dN, dE)` points in metres (e.g. exported from a design tool). It's
auto-centred and resampled to the fleet size `n` (subsampled if fewer drones, padded
on an outer ring if more — faithful when `n ≈ point count`).

```csv
# patterns/logo.csv   (dN,dE per row; '#' lines are comments)
-3.0,-2.0
-3.0, 2.0
 3.0, 0.0
```
```json
// patterns/logo.json
{ "points": [[-3.0, -2.0], [-3.0, 2.0], [3.0, 0.0]] }
```
(A bare top-level JSON array `[[dN,dE], …]` also works.)

## Notes
- Offsets are **N (north), E (east)** in metres; the show's altitude/timing are handled elsewhere.
- `get_formation(spec, n, min_spacing_m)` applies an optional uniform scale-up *after* your
  pattern so neighbours clear the planned separation — your generator just returns the shape.
- `text` is special: reached via the `text:HELLO[:scale=N]` spec, not a bare name.
- See the package docstrings in `../base.py` and `../dispatch.py` for the full contract.
