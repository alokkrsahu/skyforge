"""Tests for assign_nocross path-crossing elimination."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.assignment import (
    _count_crossings, _min_separation, _segments_cross, assign_nocross,
)


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


# ── Time-parameterised separation (collinear / same-line collisions) ──────────

def test_min_separation_collinear_collision():
    """(4,4)→(2,2) and (6,6)→(0,0) are collinear, same direction, different speed —
    they meet at (3,3). _segments_cross misses it; _min_separation must catch it."""
    assert _segments_cross((4, 4), (2, 2), (6, 6), (0, 0)) is False   # the blind spot
    assert _min_separation((4, 4), (2, 2), (6, 6), (0, 0)) < 1e-6     # actually collide


def test_min_separation_parallel_translation_safe():
    """Same offset, same direction & speed → constant clearance, never collide."""
    assert _min_separation((4, 4), (0, 0), (6, 6), (2, 2)) > 2.8


def test_collinear_swap_repaired():
    """The exact four-drone act-7 geometry that used to collide at (3,3): the
    separation-repair must re-pair the diagonal drones so no pair passes < 1.5 m."""
    pos = [(4, 4), (6, 6), (6, 4), (4, 6)]
    tgt = [(0, 0), (0, 2), (2, 0), (2, 2)]
    asgn = assign_nocross(pos, tgt, min_sep_m=1.5)
    worst = min(
        _min_separation(pos[i], tgt[asgn[i]], pos[j], tgt[asgn[j]])
        for i in range(len(asgn)) for j in range(i + 1, len(asgn))
    )
    assert worst >= 1.5
