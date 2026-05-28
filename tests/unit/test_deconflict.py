"""Unit tests for compiler.deconflict (Phase 3)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.deconflict import DeconflictConfig, deconflict
from compiler.trajectory_generator import fit_trajectory
from core.geometry import distance_3d
from core.show_format.schema import (
    Color, DroneEnvelope, DroneSpec, EnvelopeSegment, LedKeyframe, LedTrack,
    NominalTrajectory, ShowFile, ShowMetadata, Vec3,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_show(trajs: list[NominalTrajectory], duration: float = 20.0) -> ShowFile:
    n = len(trajs)
    for i, t in enumerate(trajs):
        trajs[i] = t.__class__.__new__(t.__class__)
        trajs[i].__dict__.update(t.__dict__)
        trajs[i].drone_id = i
    return ShowFile(
        metadata=ShowMetadata(n_drones=n, duration_s=duration),
        drones=[DroneSpec(logical_id=i, home_ned=Vec3()) for i in range(n)],
        trajectories=trajs,
        led_tracks=[
            LedTrack(drone_id=i, keyframes=[LedKeyframe(0.0, Color())])
            for i in range(n)
        ],
        envelopes=[
            DroneEnvelope(drone_id=i, segments=[EnvelopeSegment(0.0, duration, 0.0)])
            for i in range(n)
        ],
        reactive_bindings=[],
    )


def _min_sep(show: ShowFile, sample_hz: float = 20.0) -> float:
    """Return the minimum pairwise 3-D separation over the whole show."""
    n    = len(show.trajectories)
    dur  = show.metadata.duration_s
    dt   = 1.0 / sample_hz
    best = math.inf
    t = 0.0
    while t <= dur:
        for i in range(n):
            for j in range(i + 1, n):
                d = distance_3d(
                    show.trajectories[i].evaluate(t),
                    show.trajectories[j].evaluate(t),
                )
                if d < best:
                    best = d
        t += dt
    return best


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_no_conflict_returns_show_unchanged():
    """Drones 10 m apart → deconfliction makes no changes."""
    traj0 = fit_trajectory([0.0, 20.0], [Vec3(0, 0, -5), Vec3(0, 0, -5)])
    traj1 = fit_trajectory([0.0, 20.0], [Vec3(10, 0, -5), Vec3(10, 0, -5)])
    show  = _make_show([traj0, traj1])

    result = deconflict(show)

    assert result.resolved
    assert result.conflicts_found == 0
    assert result.iters_run == 0
    # Trajectories identical to input
    for i in range(2):
        orig_segs = show.trajectories[i].segments
        new_segs  = result.show.trajectories[i].segments
        assert len(orig_segs) == len(new_segs)
        for os_, ns in zip(orig_segs, new_segs):
            assert os_.coeffs_n == ns.coeffs_n
            assert os_.coeffs_e == ns.coeffs_e


def test_head_on_crossing_resolved():
    """Two drones on a direct collision course are deconflicted to >= min_sep."""
    # Drone 0: N 0→10, drone 1: N 10→0 — they cross at (5,0) at t=10s
    traj0 = fit_trajectory([0.0, 20.0], [Vec3(0, 0, -5), Vec3(10, 0, -5)])
    traj1 = fit_trajectory([0.0, 20.0], [Vec3(10, 0, -5), Vec3(0, 0, -5)])
    show  = _make_show([traj0, traj1])

    cfg    = DeconflictConfig(min_sep_m=1.5, margin_m=0.3)
    result = deconflict(show, cfg)

    assert result.resolved, f"Still conflicting after deconfliction (iters={result.iters_run})"
    assert result.conflicts_found > 0
    assert _min_sep(result.show) >= cfg.min_sep_m - 0.01   # 1cm tolerance for sampling


def test_parallel_close_paths_resolved():
    """Two drones travelling in the same direction 0.5 m apart are deconflicted."""
    traj0 = fit_trajectory([0.0, 20.0], [Vec3(0, 0, -5), Vec3(10, 0, -5)])
    traj1 = fit_trajectory([0.0, 20.0], [Vec3(0, 0.5, -5), Vec3(10, 0.5, -5)])
    show  = _make_show([traj0, traj1])

    cfg    = DeconflictConfig(min_sep_m=1.5, margin_m=0.3)
    result = deconflict(show, cfg)

    assert result.resolved
    assert _min_sep(result.show) >= cfg.min_sep_m - 0.01


def test_correction_clamped_to_max_deflection():
    """Coincident drones (0 separation) never produce correction > max_deflection_m."""
    # Both drones at the same point for the entire show
    traj0 = fit_trajectory([0.0, 20.0], [Vec3(5, 5, -5), Vec3(5, 5, -5)])
    traj1 = fit_trajectory([0.0, 20.0], [Vec3(5, 5, -5), Vec3(5, 5, -5)])
    show  = _make_show([traj0, traj1])

    cfg    = DeconflictConfig(min_sep_m=1.5, max_deflection_m=2.0)
    result = deconflict(show, cfg)

    # Each drone should have moved, but by no more than max_deflection_m from original
    for i in range(2):
        orig = show.trajectories[i].evaluate(10.0)
        new  = result.show.trajectories[i].evaluate(10.0)
        disp = math.sqrt((orig.n - new.n) ** 2 + (orig.e - new.e) ** 2)
        assert disp <= cfg.max_deflection_m + 0.05, f"drone {i} displaced {disp:.3f}m > max"


def test_led_tracks_and_metadata_unchanged():
    """Deconfliction must not touch LED tracks, reactive bindings, or metadata."""
    traj0 = fit_trajectory([0.0, 20.0], [Vec3(0, 0, -5), Vec3(10, 0, -5)])
    traj1 = fit_trajectory([0.0, 20.0], [Vec3(10, 0, -5), Vec3(0, 0, -5)])
    show  = _make_show([traj0, traj1])

    result = deconflict(show)

    assert result.show.led_tracks    is show.led_tracks
    assert result.show.reactive_bindings is show.reactive_bindings
    assert result.show.metadata      is show.metadata
    assert result.show.drones        is show.drones
