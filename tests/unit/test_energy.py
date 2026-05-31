"""
Tests for the battery/energy budget estimator (compiler/energy.py). Pure — builds a
small show directly and checks the hover-time + distance model and the reserve verdict.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.energy import estimate_energy, EnergyModel
from core.show_format.schema import (
    DroneEnvelope, DroneSpec, EnvelopeSegment, NominalTrajectory, PolySegment,
    ShowFile, ShowMetadata, Vec3,
)


def _show(duration, coeffs_n):
    # one drone; N(t) = polynomial in coeffs_n; E, D constant.
    k = len(coeffs_n)
    seg = PolySegment(0.0, duration, coeffs_n, [0.0] * k, [-5.0] + [0.0] * (k - 1))
    return ShowFile(
        metadata=ShowMetadata(n_drones=1, duration_s=duration),
        drones=[DroneSpec(logical_id=0, home_ned=Vec3())],
        trajectories=[NominalTrajectory(drone_id=0, segments=[seg])],
        led_tracks=[], envelopes=[
            DroneEnvelope(drone_id=0, segments=[EnvelopeSegment(0.0, duration, 1.0)])],
        reactive_bindings=[],
    )


def test_short_hover_show_fits():
    rep = estimate_energy(_show(120.0, [0.0]), EnergyModel(endurance_hover_s=600.0, reserve_frac=0.2))
    assert rep.fits and rep.max_used_frac < 0.3          # 120/600 = 0.20, near-zero distance


def test_long_show_over_budget():
    rep = estimate_energy(_show(540.0, [0.0]), EnergyModel(endurance_hover_s=600.0, reserve_frac=0.2))
    assert not rep.fits                                   # 540/600 = 0.90 > 0.80


def test_distance_increases_usage():
    still  = estimate_energy(_show(120.0, [0.0]),       EnergyModel())   # stationary
    moving = estimate_energy(_show(120.0, [0.0, 2.0]),  EnergyModel())   # N = 2t → 240 m of travel
    assert moving.max_used_frac > still.max_used_frac
