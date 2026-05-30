"""
Formation dispatcher — parses a spec string and resolves it to (dN, dE) offsets,
lazily loading the pattern (code or data) from ``patterns/`` on demand.

``get_formation`` keeps its exact prior behaviour and grammar; only the resolution
mechanism changed (a per-file plugin registry instead of a hand-edited dispatch dict).
"""
from __future__ import annotations

import csv
import importlib
import inspect
import json

from .base import (
    _ALIASES, _REGISTRY, PATTERNS_DIR, _PATTERNS_PKG,
    _centre, _fit_min_spacing, _pad_to, list_formations,
)


def get_formation(
    spec: "str | list[tuple[float, float]]",
    n:    int,
    min_spacing_m: float = 0.0,
    spacing_percentile: float = 0.0,
) -> list[tuple[float, float]]:
    """
    Return exactly n (dN, dE) offsets for the given formation spec.

    spec can be:
      "circle"              built-in generator
      "grid"                built-in generator
      "line"                built-in generator
      "v_shape" / "v"       built-in generator
      "star"                built-in generator
      "spiral"              built-in generator
      "diamond" / "arrow"   legacy 4-drone shapes (padded/subsampled to n)
      "text:HELLO"          sky-art text, 2 m pixel spacing
      "text:HELLO:scale=3"  sky-art text, 3 m pixel spacing
      [list of (dN,dE)]     custom positions (padded/subsampled to n)
      any pattern file under patterns/ (code .py or data .csv/.json), by name

    min_spacing_m > 0 uniformly scales the formation up (never down) so the
    spacing clears that distance — letting fixed-size generators hold large fleets
    safely. 0 disables it (raw generator output).

    spacing_percentile picks what "spacing" means (see _fit_min_spacing): 0.0
    (default) is the absolute-min hard floor every pair clears — used by the compiler
    so validated shows stay collision-guaranteed; a small positive value is the
    robust reference (ignores a few outlier-tight detail points) the live commander
    uses so a designed pattern isn't ballooned by its single tightest pair.
    """
    pts = _get_formation_raw(spec, n)
    if min_spacing_m > 0.0:
        pts = _fit_min_spacing(pts, min_spacing_m, spacing_percentile)
    return pts


def _load_code_pattern(name: str):
    """Return the generator callable for a code pattern, importing its file on the
    first request (which self-registers via @formation). None if no such .py file."""
    name = _ALIASES.get(name, name)
    if name in _REGISTRY:
        return _REGISTRY[name]
    if (PATTERNS_DIR / f"{name}.py").exists():
        importlib.import_module(f"{_PATTERNS_PKG}.{name}")   # runs @formation
        return _REGISTRY.get(name)
    return None


def _read_csv(path) -> list[tuple[float, float]]:
    pts = []
    with open(path) as f:
        for row in csv.reader(f):
            if not row or row[0].lstrip().startswith("#"):
                continue
            pts.append((float(row[0]), float(row[1])))
    return pts


def _read_json(path) -> list[tuple[float, float]]:
    with open(path) as f:
        data = json.load(f)
    pts = data["points"] if isinstance(data, dict) else data
    return [(float(p[0]), float(p[1])) for p in pts]


def _load_data_pattern(name: str):
    """Return centred (dN, dE) points for a data pattern (<name>.csv / .json), or None."""
    csv_p, json_p = PATTERNS_DIR / f"{name}.csv", PATTERNS_DIR / f"{name}.json"
    if csv_p.exists():
        return _centre(_read_csv(csv_p))
    if json_p.exists():
        return _centre(_read_json(json_p))
    return None


def _get_formation_raw(
    spec: "str | list[tuple[float, float]]",
    n:    int,
) -> list[tuple[float, float]]:
    if isinstance(spec, list):
        return _pad_to(spec, n)

    name = spec.lower()

    if name.startswith("text:"):
        parts   = name[5:].split(":")
        string  = parts[0]
        scale_m = 2.0
        for part in parts[1:]:
            if part.startswith("scale="):
                scale_m = float(part[6:])
        return _load_code_pattern("text")(string, n=n, scale_m=scale_m)

    # "grid:spacing=4" or "circle:radius_m=8" — keyword params after the name
    base, *kv_parts = name.split(":")
    base = _ALIASES.get(base, base)

    fn = _load_code_pattern(base)
    if fn is not None and base != "text":   # 'text' is reachable only via the text: prefix
        kwargs: dict = {}
        for kv in kv_parts:
            if "=" in kv:
                k, v = kv.split("=", 1)
                kwargs[k.strip()] = float(v.strip())
        valid    = set(inspect.signature(fn).parameters) - {"n"}
        filtered = {k: v for k, v in kwargs.items() if k in valid}
        return fn(n, **filtered)

    pts = _load_data_pattern(base)
    if pts is not None:
        return _pad_to(pts, n)

    raise ValueError(f"Unknown formation '{spec}'. Available: {list_formations() + ['text:...']}")
