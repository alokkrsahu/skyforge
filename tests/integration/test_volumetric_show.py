"""
Integration test for VOLUMETRIC 3D formations through the full offline compiler.
Builds a show using the `cat` sculpture (3-column cat.csv, dU 1..15 m), compiles it,
and asserts: it validates, 3D separation holds everywhere, and the hold altitudes
genuinely vary per drone (i.e. it is volumetric, not flat). Codifies the manual
verification done when the dU axis landed. Hermetic — no PX4/Gazebo/hardware.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import numpy as np

from compiler.pipeline import CompilePipeline
from compiler.sampling import sample_positions
from compiler.show_builder import ShowBuilder, SHOW_ALT_M, TAKEOFF_T
from core.show_format.schema import DroneSpec, Vec3

N = 16


def _cat_show(n=N, transition_s=18.0, hold_s=8.0):
    drones = [DroneSpec(i, Vec3(n=3.0 * (i // 4), e=3.0 * (i % 4))) for i in range(n)]
    b = ShowBuilder("Volumetric Cat", drones)
    b.add_act("cat", center_ne=(14, 14), transition_s=transition_s, hold_s=hold_s)
    return CompilePipeline().run(b), transition_s, hold_s


def test_volumetric_cat_show_validates():
    r, _, _ = _cat_show()
    assert r.show.metadata.validation_status == "validated"
    assert r.validation.passed, str(r.validation)


def test_volumetric_cat_separation_holds_in_3d():
    r, _, _ = _cat_show()
    sf = r.show
    times = np.linspace(0.0, sf.metadata.duration_s, 400)
    P = sample_positions(sf.trajectories, times)            # (n, T, 3)
    worst = min(
        np.linalg.norm(P[i] - P[j], axis=1).min()
        for i in range(N) for j in range(i + 1, N)
    )
    assert worst >= 1.5 - 1e-6, f"min 3D separation {worst:.3f} < MIN_SEP"


def test_volumetric_cat_hold_altitudes_vary():
    r, transition_s, hold_s = _cat_show()
    sf = r.show
    t_hold = TAKEOFF_T + transition_s + hold_s / 2.0        # mid-hold
    P = sample_positions(sf.trajectories, np.array([t_hold]))   # (n, 1, 3)
    ups = [-float(P[i, 0, 2]) for i in range(N)]            # up = -down
    assert max(ups) - min(ups) > 3.0                       # real vertical extent
    assert len({round(u, 1) for u in ups}) >= 3            # multiple distinct levels
    assert min(ups) >= SHOW_ALT_M - 1e-6                   # dU>=0 → never below base
