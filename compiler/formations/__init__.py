"""
Formation generators for the Skyforge compiler — a plugin package.

Each pattern is its own auto-discovered file under ``patterns/`` (code ``.py`` or
data ``.csv``/``.json``); adding one needs no edits here. See ``patterns/README.md``.

Public API (unchanged): ``get_formation(spec, n, min_spacing_m)`` resolves any spec;
``list_formations()`` returns the catalog. The core generators are re-exported below
for direct import / back-compat.

Built-in names for ShowBuilder.add_act():
    "circle", "grid", "line", "v_shape"/"v", "star", "spiral",
    "diamond", "arrow" (legacy 4-point), plus any pattern file under patterns/.
Sky-art text:  "text:HELLO"  /  "text:HELLO:scale=3.0".
Custom positions: pass a list of (dN, dE) tuples directly.
"""
from .base import formation, list_formations
from .dispatch import get_formation
from .patterns.circle import circle
from .patterns.grid import grid
from .patterns.line import line
from .patterns.v_shape import v_shape
from .patterns.star import star
from .patterns.spiral import spiral
from .patterns.text import pixel_count, text

__all__ = [
    "get_formation", "list_formations", "formation",
    "circle", "grid", "line", "v_shape", "star", "spiral", "text", "pixel_count",
]
