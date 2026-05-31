"""
Tests for the formations plugin package — auto-discovery, lazy code patterns, and
data (CSV/JSON) patterns. These drop a temp file into compiler/formations/patterns/
to prove the "add a file → it's a pattern, zero other edits" mechanism, then clean up.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.formations import get_formation, list_formations
from compiler.formations import base

PATTERNS = base.PATTERNS_DIR


def _expect_valueerror(spec):
    try:
        get_formation(spec, 4)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ── Catalog + back-compat resolution ──────────────────────────────────────────

def test_catalog_lists_every_shipped_pattern():
    cat = list_formations()
    for name in ("circle", "grid", "line", "v_shape", "v", "star",
                 "spiral", "diamond", "arrow", "text"):
        assert name in cat


def test_alias_v_equals_v_shape():
    assert get_formation("v", 5) == get_formation("v_shape", 5)


def test_unknown_formation_raises():
    _expect_valueerror("definitely_not_a_pattern")


def test_min_spacing_scales_up():
    small = get_formation("circle", 4, min_spacing_m=0.0)
    big   = get_formation("circle", 4, min_spacing_m=20.0)
    assert max(abs(v) for p in big for v in p) > max(abs(v) for p in small for v in p)


# ── Robust spacing (size off a percentile of NN distance, not the tightest pair) ──

def _radius(pts):
    return max(math.hypot(p[0], p[1]) for p in pts)   # horizontal radius (pts are 3-tuples)


def test_robust_ignores_outlier_tight_pair():
    """A designed pattern with ONE near-touching pair should NOT balloon the whole
    formation under robust mode — but DOES under the default absolute-min sizing."""
    ring = [(5 * math.cos(2 * math.pi * k / 12), 5 * math.sin(2 * math.pi * k / 12))
            for k in range(12)]
    pts = ring + [(5.2, 0.0)]                       # one 0.2 m outlier next to ring[0]=(5,0)
    big_min    = get_formation(pts, len(pts), min_spacing_m=3.0)                       # p0
    big_robust = get_formation(pts, len(pts), min_spacing_m=3.0, spacing_percentile=20.0)
    # min-mode sizes off the 0.2 m pair (factor ~15x); robust ignores that lone outlier.
    assert _radius(big_robust) < 0.5 * _radius(big_min)


def test_robust_equals_default_for_uniform():
    """A uniform pattern (equal nearest-neighbour distances) is unchanged by the
    percentile — every percentile equals the minimum."""
    default = get_formation("circle", 24, min_spacing_m=3.0)
    robust  = get_formation("circle", 24, min_spacing_m=3.0, spacing_percentile=20.0)
    # equal to float precision (circle's NN distances tie only up to cos/sin rounding)
    assert all(abs(a - b) < 1e-6 for d, r in zip(default, robust) for a, b in zip(d, r))


# ── Lazy code-pattern discovery (drop a .py → usable) ─────────────────────────

def test_lazy_code_pattern_discovered_and_callable():
    name = "tmp_diag_code"
    path = PATTERNS / f"{name}.py"
    path.write_text(
        "from ..base import formation\n\n"
        "@formation\n"
        f"def {name}(n, step=1.0):\n"
        "    return [(i * step, i * step) for i in range(n)]\n"
    )
    try:
        assert name in list_formations()                  # folder scan sees it
        pts = get_formation(name, 5)                       # lazy import + call
        assert len(pts) == 5 and pts[0] == (0.0, 0.0, 0.0)   # 3-tuple, flat (dU=0)
        pts2 = get_formation(f"{name}:step=2.0", 3)        # kwarg passthrough
        assert pts2[1] == (2.0, 2.0, 0.0)
    finally:
        path.unlink()
        sys.modules.pop(f"compiler.formations.patterns.{name}", None)
        base._REGISTRY.pop(name, None)


# ── Data patterns (CSV / JSON point-clouds) ───────────────────────────────────

def test_csv_data_pattern_loads_and_centres():
    name = "tmp_square_csv"
    path = PATTERNS / f"{name}.csv"
    path.write_text("# a unit square\n-1,-1\n-1,1\n1,1\n1,-1\n")
    try:
        assert name in list_formations()
        pts = get_formation(name, 4)
        assert len(pts) == 4
        assert abs(sum(p[0] for p in pts)) < 1e-9          # centred on origin
        assert abs(sum(p[1] for p in pts)) < 1e-9
    finally:
        path.unlink()


def test_json_data_pattern_object_and_resample():
    name = "tmp_tri_json"
    path = PATTERNS / f"{name}.json"
    path.write_text(json.dumps({"points": [[0, 0], [4, 0], [2, 4]]}))
    try:
        assert name in list_formations()
        assert len(get_formation(name, 3)) == 3            # exact
        assert len(get_formation(name, 8)) == 8            # padded to N
    finally:
        path.unlink()


def test_json_data_pattern_bare_list():
    name = "tmp_tri2_json"
    path = PATTERNS / f"{name}.json"
    path.write_text(json.dumps([[0, 0], [4, 0], [2, 4], [6, 6]]))
    try:
        assert len(get_formation(name, 2)) == 2            # subsampled
    finally:
        path.unlink()


# ── Volumetric 3D (the optional dU third column) ──────────────────────────────

def test_get_formation_returns_3tuples():
    pts = get_formation("circle", 6)
    assert all(len(p) == 3 for p in pts)                   # always 3-tuples
    assert all(p[2] == 0.0 for p in pts)                   # flat generator → dU=0


def test_csv_3col_is_volumetric():
    name = "tmp_vol_csv"
    path = PATTERNS / f"{name}.csv"
    path.write_text("# volumetric\n-1,-1,2\n-1,1,4\n1,1,6\n1,-1,8\n")
    try:
        pts = get_formation(name, 4)
        assert sorted(round(p[2], 3) for p in pts) == [2.0, 4.0, 6.0, 8.0]   # dU preserved
        assert abs(sum(p[0] for p in pts)) < 1e-9 and abs(sum(p[1] for p in pts)) < 1e-9  # N,E centred
    finally:
        path.unlink()


def test_csv_2col_stays_flat():
    name = "tmp_flat_csv"
    path = PATTERNS / f"{name}.csv"
    path.write_text("-1,-1\n1,1\n")
    try:
        assert all(p[2] == 0.0 for p in get_formation(name, 2))   # 2-col → dU=0
    finally:
        path.unlink()


def test_negative_du_is_clamped_to_zero():
    # floor safety: a stray negative dU in a data file must never fly below the base
    name = "tmp_negdu_csv"
    path = PATTERNS / f"{name}.csv"
    path.write_text("-1,-1,-10\n1,1,5\n")
    try:
        us = sorted(p[2] for p in get_formation(name, 2))
        assert us == [0.0, 5.0]                            # -10 clamped to 0
    finally:
        path.unlink()


def test_all_generators_return_2tuples_on_direct_call():
    # direct generator calls honour the flat (dN, dE) contract uniformly; get_formation
    # is what normalises to 3-tuples. (Regression: v_shape/star/text once leaked 3-tuples.)
    from compiler.formations import circle, grid, line, v_shape, star, spiral, text
    for fn in (circle, grid, line, v_shape, star, spiral):
        assert all(len(p) == 2 for p in fn(7)), f"{fn.__name__} should return 2-tuples"
    assert all(len(p) == 2 for p in text("AB"))


def test_centre_leaves_du_untouched():
    out = base._centre([(0.0, 0.0, 5.0), (4.0, 4.0, 9.0)])
    assert [p[2] for p in out] == [5.0, 9.0]               # only N,E recentred
    assert abs(sum(p[0] for p in out)) < 1e-9 and abs(sum(p[1] for p in out)) < 1e-9


def test_fit_min_spacing_scales_all_three_axes():
    # two points 1 m apart in 3D (incl. dU) → scaled so they clear 4 m, aspect kept
    out = base._fit_min_spacing([(0.0, 0.0, 0.0), (0.0, 0.0, 1.0)], 4.0)
    assert abs(out[1][2] - 4.0) < 1e-9                     # dU scaled up too (1 → 4)
    out2 = base._fit_min_spacing([(0.0, 0.0, 1.0), (1.0, 0.0, 1.0)], 3.0)
    assert abs(out2[0][2] - 3.0) < 1e-9 and abs(out2[1][2] - 3.0) < 1e-9  # dU scales with XY
