"""
Plugin core for Skyforge formations.

Each pattern lives in its own file under ``patterns/`` and is auto-discovered —
adding a pattern needs ZERO edits here. Two kinds are supported:

  * Code pattern: ``patterns/<name>.py`` with a ``@formation``-decorated generator
    ``def <name>(n, **params) -> list[(dN, dE)]`` (metres, centred on origin).
  * Data pattern: ``patterns/<name>.csv`` (``dN,dE`` rows) or ``patterns/<name>.json``
    (``[[dN,dE], …]`` or ``{"points": […]}``) — a designed point-cloud, resampled to n.

Patterns are loaded LAZILY (only when requested), so thousands of files don't slow
startup. ``list_formations()`` is a cheap directory scan — the folder IS the catalog.
The public API (``get_formation``, ``list_formations``, the core generators) is
re-exported from the package ``__init__``.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

PATTERNS_DIR  = Path(__file__).resolve().parent / "patterns"
_PATTERNS_PKG = f"{__package__}.patterns"

_REGISTRY: dict = {}                 # name -> generator callable (filled lazily on import)
_ALIASES:  dict = {"v": "v_shape"}   # rare; filename is normally the canonical name


def formation(func=None, *, name=None, aliases=(), description=""):
    """Register a pattern generator. Use bare ``@formation`` or
    ``@formation(aliases=("v",), description="…")``. The registered name defaults
    to the function name, which should match the file name."""
    def deco(fn):
        key = (name or fn.__name__).lower()
        _REGISTRY[key] = fn
        fn._formation_name = key
        fn._formation_desc = description or (fn.__doc__ or "").strip().splitlines()[:1]
        for a in aliases:
            _ALIASES[a.lower()] = key
        return fn
    return deco(func) if callable(func) else deco


def list_formations() -> list[str]:
    """Every available pattern name — a cheap scan of ``patterns/`` (code ``.py`` +
    data ``.csv``/``.json``) plus aliases. No imports, so it scales to thousands."""
    names = set(_ALIASES)
    if PATTERNS_DIR.is_dir():
        for p in PATTERNS_DIR.iterdir():
            if p.name.startswith(("_", ".")) or p.name == "__init__.py":
                continue
            if p.suffix in (".py", ".csv", ".json"):
                names.add(p.stem.lower())
    return sorted(names)


# ── Shared geometry helpers (used by pattern generators + the dispatcher) ───────

def _fit_min_spacing(
    pts: list[tuple[float, float]],
    min_spacing_m: float,
    spacing_percentile: float = 0.0,
) -> list[tuple[float, float]]:
    """
    Uniformly scale a formation (about the origin) so its spacing clears
    min_spacing_m. NEVER shrinks (factor >= 1), so a formation that already fits — or
    a small fleet — is returned unchanged. This is what lets a fixed-radius generator
    hold an arbitrary fleet size: e.g. a 100-drone circle is blown up from r=5 m
    (0.3 m spacing) to ~28 m so neighbours clear the planned separation.

    ``spacing_percentile`` chooses what "spacing" means — the reference is that
    percentile of the per-point nearest-neighbour distances:

      * ``0.0`` (default) → the ABSOLUTE minimum pair (every pair ends ≥ min_spacing_m).
        This is the hard floor the compiler/validator path relies on; behaviour is
        byte-for-byte unchanged from sizing off ``dist.min()``.
      * a small positive value (e.g. 10–25) → a ROBUST reference that ignores a handful
        of outlier-tight pairs, so a *designed* pattern with a few near-touching detail
        points (e.g. a cat's ears/eyes) isn't ballooned by its single tightest pair.
        Used by the live commander, where assign_nocross + APF are the reactive backstop
        for the few sub-spacing feature points. For a uniform pattern (circle/grid, equal
        nearest-neighbour distances) every percentile equals the min, so this is a no-op;
        a non-uniform pattern (e.g. star) resizes mildly.

    Coincident points (reference spacing 0) cannot be separated by scaling and are left
    as-is — validation will flag the degenerate formation.
    """
    n = len(pts)
    if n < 2 or min_spacing_m <= 0.0:
        return pts
    P = np.asarray(pts, dtype=float)                      # (n, 2)
    diff = P[:, None, :] - P[None, :, :]
    dist = np.hypot(diff[:, :, 0], diff[:, :, 1])
    np.fill_diagonal(dist, np.inf)
    nn = dist.min(axis=1)                                 # per-point nearest neighbour
    ref = float(np.percentile(nn, spacing_percentile))    # q=0 → nn.min() → absolute min
    if ref <= 1e-9:
        return pts
    factor = max(1.0, min_spacing_m / ref)
    if factor == 1.0:
        return pts
    return [(p[0] * factor, p[1] * factor) for p in pts]


def _centre(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not pts:
        return pts
    cn = sum(p[0] for p in pts) / len(pts)
    ce = sum(p[1] for p in pts) / len(pts)
    return [(p[0] - cn, p[1] - ce) for p in pts]


def _pad_to(pts: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """
    Return exactly n positions.
    - Surplus (m > n): subsample indices evenly so shape stays representative.
    - Deficit (m < n): append extra drones on a ring just outside the formation.
    """
    m = len(pts)
    if m == n:
        return list(pts)
    if m > n:
        if n == 1:
            return [pts[0]]
        indices = [round(i * (m - 1) / (n - 1)) for i in range(n)]
        return [pts[i] for i in indices]
    r_ring = (max(math.hypot(p[0], p[1]) for p in pts) if pts else 0.0) + 3.0
    extra = n - m
    ring = [
        (r_ring * math.cos(math.pi / 2 - k * 2 * math.pi / extra),
         r_ring * math.sin(math.pi / 2 - k * 2 * math.pi / extra))
        for k in range(extra)
    ]
    return list(pts) + ring
