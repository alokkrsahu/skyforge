"""
Unit tests for the compiler's 3D (volumetric) altitude composition in
ShowBuilder._append_transition: flat formations stay byte-for-byte at show altitude,
volumetric formations get per-drone hold altitudes (-SHOW_ALT_M - dU), and a
volumetric transition routes its cross phase ABOVE the whole envelope.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.show_builder import LAYER_SPACING_M, SHOW_ALT_M, ShowBuilder
from core.show_format.schema import DroneSpec, Vec3


def _builder(n=2):
    drones = [DroneSpec(i, Vec3(n=0.0, e=5.0 * i)) for i in range(n)]
    return ShowBuilder("T", drones)


def test_flat_formation_arrives_at_show_alt():
    """Flat (dU=0) hold altitude is exactly -SHOW_ALT_M — unchanged from pre-3D."""
    b = _builder()
    wps = [[], []]
    _, new_u = b._append_transition(
        wps, [(0, 0), (5, 0)], [0.0, 0.0],
        [(0.0, 0.0, 0.0), (5.0, 0.0, 0.0)], [0, 1], [0, 0], 0.0, 10.0)
    for i in range(2):
        assert abs(wps[i][-1][1].d - (-SHOW_ALT_M)) < 1e-9
    assert new_u == [0.0, 0.0]


def test_volumetric_arrive_uses_per_drone_du():
    """Volumetric hold altitude is -SHOW_ALT_M - dU per drone."""
    b = _builder()
    wps = [[], []]
    _, new_u = b._append_transition(
        wps, [(0, 0), (5, 0)], [0.0, 0.0],
        [(0.0, 0.0, 8.0), (5.0, 0.0, 3.0)], [0, 1], [0, 0], 0.0, 10.0)
    assert abs(wps[0][-1][1].d - (-SHOW_ALT_M - 8.0)) < 1e-9
    assert abs(wps[1][-1][1].d - (-SHOW_ALT_M - 3.0)) < 1e-9
    assert new_u == [8.0, 3.0]


def test_flat_banded_transition_byte_for_byte():
    """A flat banded transition keeps the original band altitude (no transit ceiling)."""
    b = _builder()
    wps = [[], []]
    b._append_transition(
        wps, [(0, 0), (5, 0)], [0.0, 0.0],
        [(0.0, 0.0, 0.0), (5.0, 0.0, 0.0)], [0, 1], [1, 0], 0.0, 10.0)
    cross_d = wps[0][1][1].d                       # band-1 drone: [climb, cross, arrive]
    assert abs(cross_d - (-SHOW_ALT_M - 1 * LAYER_SPACING_M)) < 1e-9


def test_volumetric_transition_routes_above_envelope():
    """A volumetric banded transition's cross phase clears every hold altitude."""
    b = _builder()
    wps = [[], []]
    b._append_transition(
        wps, [(0, 0), (5, 0)], [0.0, 0.0],
        [(0.0, 0.0, 8.0), (5.0, 0.0, 3.0)], [0, 1], [1, 0], 0.0, 10.0)
    cross_d   = wps[0][1][1].d                      # banded drone's cross altitude (down)
    arrive_ds = [wps[0][-1][1].d, wps[1][-1][1].d]  # hold altitudes (down)
    # "down" more negative == higher: cross must be above (more negative than) every hold
    assert cross_d < min(arrive_ds) - 1e-9
