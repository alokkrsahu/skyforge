"""Integration tests: full CompilePipeline on the four-drone demo show.

Architecture note
-----------------
The demo show's nominal (APF-free) trajectories can have close approaches
during formation transitions — the runtime APF layer handles those at flight
time.  Tests here verify pipeline *behaviour*, not whether the specific demo
choreography passes the operational-clearance threshold.

Tests that exercise the validator use a hand-crafted show guaranteed to be
well-separated at the nominal level.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from compiler.envelope import EnvelopeConfig
from compiler.pipeline import CompileConfig, CompilePipeline
from compiler.validator import ValidationConfig, validate
from core.show_format.reader import from_json
from core.show_format.schema import (
    Color, DroneEnvelope, DroneSpec, EnvelopeSegment, LedKeyframe, LedTrack,
    NominalTrajectory, PolySegment, ShowFile, ShowMetadata, Vec3,
)
from core.show_format.writer import to_json


# ── Helper: a show whose drones are guaranteed well-separated ─────────────────

def _safe_show(duration: float = 20.0) -> ShowFile:
    """Four drones at (0,0), (0,10), (10,0), (10,10) — 10 m apart on all axes."""
    homes = [(0, 0, -5), (0, 10, -5), (10, 0, -5), (10, 10, -5)]
    trajs = [
        NominalTrajectory(
            drone_id=i,
            segments=[PolySegment(
                t_start=0.0, t_end=duration,
                coeffs_n=[float(n)], coeffs_e=[float(e)], coeffs_d=[float(d)],
            )],
        )
        for i, (n, e, d) in enumerate(homes)
    ]
    return ShowFile(
        metadata=ShowMetadata(n_drones=4, duration_s=duration),
        drones=[DroneSpec(logical_id=i, home_ned=Vec3()) for i in range(4)],
        trajectories=trajs,
        led_tracks=[
            LedTrack(drone_id=i, keyframes=[LedKeyframe(0.0, Color())])
            for i in range(4)
        ],
        envelopes=[
            DroneEnvelope(drone_id=i, segments=[EnvelopeSegment(0.0, duration, 0.0)])
            for i in range(4)
        ],
        reactive_bindings=[],
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_pipeline_runs_and_produces_validation_report():
    """Pipeline compiles the demo show with deconfliction and returns a ValidationResult."""
    from shows.four_drone_demo import builder

    result = CompilePipeline(
        CompileConfig(deconflict=True, fail_on_error=False)
    ).run(builder)

    assert result.show is not None
    assert result.show.metadata.n_drones == 4
    assert result.validation is not None


def test_pipeline_envelopes_replace_placeholders():
    """All envelope radii are non-negative after the pipeline runs."""
    from shows.four_drone_demo import builder

    result = CompilePipeline(CompileConfig(deconflict=True, validate=False)).run(builder)
    assert result.ok
    for env in result.show.envelopes:
        for seg in env.segments:
            assert seg.radius_m >= 0.0, (
                f"drone {env.drone_id}: negative radius in segment "
                f"[{seg.t_start:.1f}, {seg.t_end:.1f}]"
            )


def test_pipeline_json_round_trip_preserves_envelopes():
    """Envelope segments and radii survive JSON serialisation → deserialisation."""
    from shows.four_drone_demo import builder

    result = CompilePipeline(CompileConfig(deconflict=True, validate=False)).run(builder)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
    try:
        to_json(result.show, path)
        loaded = from_json(path)
        for i in range(loaded.metadata.n_drones):
            orig_segs   = result.show.envelopes[i].segments
            loaded_segs = loaded.envelopes[i].segments
            assert len(loaded_segs) == len(orig_segs)
            for o, l in zip(orig_segs, loaded_segs):
                assert abs(o.radius_m - l.radius_m) < 1e-6
    finally:
        os.unlink(path)


def test_safe_show_passes_validation():
    """A show with 10 m inter-drone separation must pass full validation."""
    show   = _safe_show()
    result = validate(show, ValidationConfig(min_sep_m=1.5))
    assert result.passed, str(result)
    assert result.errors == []


def test_pipeline_marks_validated_status_on_safe_show():
    """validation_status is set to 'validated' when the show passes."""
    show = _safe_show()

    # Wrap the safe show in a pipeline by creating a minimal stub builder
    # that returns the pre-built show directly.
    from compiler.show_builder import ShowBuilder
    from core.show_format.schema import DroneSpec, Vec3

    class _FixedBuilder(ShowBuilder):
        def __init__(self, fixed_show):
            self._fixed = fixed_show

        def compile(self):
            return self._fixed

    result = CompilePipeline().run(_FixedBuilder(show))
    assert result.ok, str(result.validation)
    assert result.show.metadata.validation_status == "validated"


def test_pipeline_deconflicts_crossing_show():
    """A show with head-on crossing drones compiles clean when deconfliction is on."""
    from compiler.trajectory_generator import fit_trajectory

    # Two drones on a direct collision course (cross at t=10s)
    duration = 20.0
    traj0 = fit_trajectory([0.0, duration], [Vec3(0, 0, -5), Vec3(10, 0, -5)])
    traj1 = fit_trajectory([0.0, duration], [Vec3(10, 0, -5), Vec3(0, 0, -5)])
    traj0.drone_id = 0
    traj1.drone_id = 1

    show = ShowFile(
        metadata=ShowMetadata(n_drones=2, duration_s=duration),
        drones=[DroneSpec(logical_id=i, home_ned=Vec3()) for i in range(2)],
        trajectories=[traj0, traj1],
        led_tracks=[
            LedTrack(drone_id=i, keyframes=[LedKeyframe(0.0, Color())])
            for i in range(2)
        ],
        envelopes=[
            DroneEnvelope(drone_id=i, segments=[EnvelopeSegment(0.0, duration, 0.0)])
            for i in range(2)
        ],
        reactive_bindings=[],
    )

    from compiler.show_builder import ShowBuilder

    class _CrossingBuilder(ShowBuilder):
        def __init__(self, s): self._s = s
        def compile(self): return self._s

    result = CompilePipeline(CompileConfig(deconflict=True)).run(_CrossingBuilder(show))
    assert result.ok, str(result.validation)
    assert result.validation.errors == []
    assert result.show.metadata.validation_status == "validated"
