"""Tests for compiler/formations.py — formation generators and sky-art text."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.formations import (
    circle, get_formation, grid, line, pixel_count, spiral, star, text,
    v_shape,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _centroid(pts):
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


# ── Circle ────────────────────────────────────────────────────────────────────

def test_circle_count():
    assert len(circle(10)) == 10
    assert len(circle(100)) == 100


def test_circle_radius():
    pts = circle(8, radius_m=5.0)
    for n, e in pts:
        assert abs(math.hypot(n, e) - 5.0) < 1e-9


def test_circle_centred():
    cn, ce = _centroid(circle(12, radius_m=4.0))
    assert abs(cn) < 1e-9 and abs(ce) < 1e-9


# ── Grid ──────────────────────────────────────────────────────────────────────

def test_grid_count():
    for n in (1, 4, 9, 10, 100):
        assert len(grid(n)) == n


def test_grid_centred():
    cn, ce = _centroid(grid(9, spacing_m=2.0))
    assert abs(cn) < 1e-9 and abs(ce) < 1e-9


# ── Line ──────────────────────────────────────────────────────────────────────

def test_line_all_north_zero():
    for n, e in line(5):
        assert n == 0.0


def test_line_symmetric():
    pts = line(5, spacing_m=2.0)
    assert pts[0][1] == -4.0 and pts[-1][1] == 4.0


# ── Star ──────────────────────────────────────────────────────────────────────

def test_star_count():
    assert len(star(10)) == 10
    assert len(star(3)) == 3


# ── Text ─────────────────────────────────────────────────────────────────────

def test_pixel_count_alok():
    assert pixel_count("ALOK") == 59


def test_text_returns_pixels_when_n_none():
    pts = text("A")
    assert len(pts) == pixel_count("A")


def test_text_pads_to_n():
    pts = text("A", n=100)
    assert len(pts) == 100


def test_text_subsamples_to_n():
    full = pixel_count("ALOK")
    pts  = text("ALOK", n=full - 10)
    assert len(pts) == full - 10


def test_text_centred():
    pts = text("ALOK")
    cn, ce = _centroid(pts)
    assert abs(cn) < 0.1 and abs(ce) < 0.1


def test_text_scale_doubles_extent():
    pts1 = text("A", scale_m=2.0)
    pts2 = text("A", scale_m=4.0)
    ext1 = max(math.hypot(p[0], p[1]) for p in pts1)
    ext2 = max(math.hypot(p[0], p[1]) for p in pts2)
    assert abs(ext2 / ext1 - 2.0) < 0.1


# ── get_formation dispatcher ──────────────────────────────────────────────────

def test_get_formation_circle():
    pts = get_formation("circle", 20)
    assert len(pts) == 20


def test_get_formation_text():
    pts = get_formation("text:ALOK", 100)
    assert len(pts) == 100


def test_get_formation_text_scale():
    pts1 = get_formation("text:A", 50)
    pts2 = get_formation("text:A:scale=4.0", 50)
    ext1 = max(math.hypot(p[0], p[1]) for p in pts1)
    ext2 = max(math.hypot(p[0], p[1]) for p in pts2)
    assert ext2 > ext1


def test_get_formation_custom_list():
    custom = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    pts = get_formation(custom, 3)
    assert pts == custom


def test_get_formation_legacy_diamond():
    pts = get_formation("diamond", 4)
    assert len(pts) == 4


def test_get_formation_unknown_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown formation"):
        get_formation("hexagon", 6)
