"""Tests for assign_nocross path-crossing elimination."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.assignment import _count_crossings, _segments_cross, assign_nocross


def test_segments_cross_basic():
    assert _segments_cross((0, 0), (1, 1), (0, 1), (1, 0)) is True


def test_segments_no_cross_parallel():
    assert _segments_cross((0, 0), (1, 0), (0, 1), (1, 1)) is False


def test_segments_no_cross_t_shape():
    """T-junction: segments share an endpoint plane but do not cross."""
    assert _segments_cross((0, 0), (0, 1), (0, 1), (1, 1)) is False


def test_no_crossings_identity():
    pos = [(0, 0), (0, 10), (10, 0), (10, 10)]
    tgt = [(0, 0), (0, 10), (10, 0), (10, 10)]
    asgn = assign_nocross(pos, tgt)
    assert _count_crossings(pos, tgt, asgn) == 0


def test_head_on_swap_eliminated():
    """Two drones heading straight for each other's start — crossing must be removed."""
    pos = [(0, 0), (10, 0)]
    tgt = [(10, 0), (0, 0)]
    asgn = assign_nocross(pos, tgt)
    assert _count_crossings(pos, tgt, asgn) == 0


def test_four_drone_cross_eliminated():
    """Four drones in a pattern whose naive assignment crosses — swap removes it."""
    # Drones at corners; targets rotated 90°: crossing assignment expected from Hungarian
    pos = [(0, 0), (0, 4), (4, 0), (4, 4)]
    tgt = [(4, 0), (4, 4), (0, 0), (0, 4)]
    asgn = assign_nocross(pos, tgt)
    assert _count_crossings(pos, tgt, asgn) == 0


def test_already_optimal_unchanged():
    """If Hungarian result has no crossings it is returned as-is."""
    pos = [(0, 0), (0, 2), (2, 0), (2, 2)]
    # Targets nearby own position — no crossing expected
    tgt = [(0.5, 0.5), (0.5, 2.5), (2.5, 0.5), (2.5, 2.5)]
    asgn = assign_nocross(pos, tgt)
    assert _count_crossings(pos, tgt, asgn) == 0
