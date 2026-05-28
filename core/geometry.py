"""
Shared 3D vector geometry.
Centralises distance/norm math used by both the compiler and the runtime.
"""
from __future__ import annotations

import math

from core.show_format.schema import Vec3


def norm(v: Vec3) -> float:
    """Euclidean magnitude of a Vec3."""
    return math.sqrt(v.n * v.n + v.e * v.e + v.d * v.d)


def distance_3d(a: Vec3, b: Vec3) -> float:
    return math.sqrt((a.n - b.n) ** 2 + (a.e - b.e) ** 2 + (a.d - b.d) ** 2)


def distance_2d(a: Vec3, b: Vec3) -> float:
    """Horizontal (NE-plane) distance — ignores altitude."""
    return math.sqrt((a.n - b.n) ** 2 + (a.e - b.e) ** 2)
