"""
Integration tests for the `skyforge` CLI (cli.py) — the compile/validate/info
entry points. The cmd_* handlers return an int exit code, so we call them directly
with a built argparse.Namespace (no subprocess, fully hermetic — pure compiler path,
no PX4/Gazebo/hardware). Covers success paths, exit codes, flags, and error paths.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import cli
from core.show_format.reader import from_json
from core.show_format.schema import (
    Color, DroneEnvelope, DroneSpec, EnvelopeSegment, LedKeyframe, LedTrack,
    NominalTrajectory, PolySegment, ShowFile, ShowMetadata, Vec3,
)
from core.show_format.writer import to_json

REPO = os.path.join(os.path.dirname(__file__), "../..")
DEMO = os.path.abspath(os.path.join(REPO, "shows", "four_drone_demo.py"))


def _compile_ns(script, out_dir, min_sep=1.5, no_validate=False):
    return argparse.Namespace(script=script, output=str(out_dir),
                              min_sep=min_sep, no_validate=no_validate)


# ── compile ───────────────────────────────────────────────────────────────────

def test_compile_writes_outputs_and_validates(tmp_path):
    rc = cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    assert rc == 0
    j = tmp_path / "four_drone_demo.skyforge.json"
    b = tmp_path / "four_drone_demo.skyforge"
    assert j.exists() and b.exists()
    assert from_json(str(j)).metadata.validation_status == "validated"


def test_compile_missing_builder_returns_1(tmp_path):
    script = tmp_path / "no_builder.py"
    script.write_text("x = 1\n")
    assert cli.cmd_compile(_compile_ns(str(script), tmp_path)) == 1


def test_compile_unknown_formation_returns_1(tmp_path):
    script = tmp_path / "bad_formation.py"
    script.write_text(
        "from compiler.show_builder import ShowBuilder\n"
        "from core.show_format.schema import DroneSpec, Vec3\n"
        "drones = [DroneSpec(i, Vec3(n=2.0*(i//2), e=2.0*(i%2))) for i in range(4)]\n"
        "builder = ShowBuilder('Bad', drones)\n"
        "builder.add_act('hexagon', center_ne=(8, 8), transition_s=12, hold_s=6)\n"
    )
    # get_formation('hexagon') raises ValueError at compile → handler returns 1, no crash
    assert cli.cmd_compile(_compile_ns(str(script), tmp_path)) == 1
    assert not (tmp_path / "bad_formation.skyforge.json").exists()   # nothing written


def test_compile_missing_script_returns_1(tmp_path):
    assert cli.cmd_compile(_compile_ns(str(tmp_path / "nope.py"), tmp_path)) == 1


def test_compile_no_validate_writes_without_gate(tmp_path):
    rc = cli.cmd_compile(_compile_ns(DEMO, tmp_path, no_validate=True))
    assert rc == 0
    assert (tmp_path / "four_drone_demo.skyforge.json").exists()


# ── validate ────────────────────────────────────────────────────────────────────

def test_validate_passes_on_validated_show(tmp_path):
    cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    j = tmp_path / "four_drone_demo.skyforge.json"
    assert cli.cmd_validate(argparse.Namespace(show=str(j), min_sep=1.5)) == 0


def test_validate_fails_on_too_close_show(tmp_path):
    # two coincident drones → separation error → exit 1
    seg = lambda: PolySegment(t_start=0.0, t_end=10.0, coeffs_n=[0.0], coeffs_e=[0.0], coeffs_d=[-5.0])
    show = ShowFile(
        metadata=ShowMetadata(n_drones=2, duration_s=10.0),
        drones=[DroneSpec(logical_id=i, home_ned=Vec3()) for i in range(2)],
        trajectories=[NominalTrajectory(drone_id=i, segments=[seg()]) for i in range(2)],
        led_tracks=[LedTrack(drone_id=i, keyframes=[LedKeyframe(0.0, Color())]) for i in range(2)],
        envelopes=[
            DroneEnvelope(drone_id=i, segments=[EnvelopeSegment(0.0, 10.0, 1.0)]) for i in range(2)
        ], reactive_bindings=[],
    )
    j = tmp_path / "too_close.skyforge.json"
    to_json(show, str(j))
    assert cli.cmd_validate(argparse.Namespace(show=str(j), min_sep=1.5)) == 1


def test_validate_missing_file_returns_1(tmp_path):
    assert cli.cmd_validate(argparse.Namespace(show=str(tmp_path / "nope.json"), min_sep=1.5)) == 1


# ── info ─────────────────────────────────────────────────────────────────────────

def test_export_single_drone_slice(tmp_path):
    cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    j = tmp_path / "four_drone_demo.skyforge.json"
    rc = cli.cmd_export(argparse.Namespace(show=str(j), drone=0, all=False, output=str(tmp_path)))
    assert rc == 0
    slice_path = tmp_path / "four_drone_demo.drone000.skyforge.json"
    assert slice_path.exists()
    sf = from_json(str(slice_path))                      # round-trips reader validation
    assert sf.metadata.n_drones == 1 and len(sf.trajectories) == 1


def test_export_all_slices(tmp_path):
    cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    j = tmp_path / "four_drone_demo.skyforge.json"
    n = from_json(str(j)).metadata.n_drones
    rc = cli.cmd_export(argparse.Namespace(show=str(j), drone=None, all=True, output=str(tmp_path)))
    assert rc == 0
    assert all((tmp_path / f"four_drone_demo.drone{i:03d}.skyforge.json").exists() for i in range(n))


def test_export_bad_drone_returns_1(tmp_path):
    cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    j = tmp_path / "four_drone_demo.skyforge.json"
    assert cli.cmd_export(argparse.Namespace(show=str(j), drone=99, all=False, output=str(tmp_path))) == 1


def test_preflight_go_on_validated_show(tmp_path):
    cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    j = tmp_path / "four_drone_demo.skyforge.json"
    rc = cli.cmd_preflight(argparse.Namespace(show=str(j), min_sep=1.5,
                                              tracking_margin=0.0, endurance=600.0))
    assert rc == 0                                       # validated + fits battery → GO


def test_preflight_nogo_over_battery_budget(tmp_path):
    cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    j = tmp_path / "four_drone_demo.skyforge.json"
    rc = cli.cmd_preflight(argparse.Namespace(show=str(j), min_sep=1.5,
                                              tracking_margin=0.0, endurance=1.0))  # 1 s endurance
    assert rc == 1                                       # battery NO-GO


def test_flightlog_summary(tmp_path):
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO, "runtime"))
    from show.fleet_monitor import BlackBox
    log = tmp_path / "bb.jsonl"
    bb = BlackBox(str(log))
    bb.record({"t": 0.0, "n_lost": 0, "max_pos_error_m": 0.5, "min_battery_frac": 0.9})
    bb.record({"t": 2.0, "n_lost": 1, "max_pos_error_m": 1.2, "min_battery_frac": 0.7})
    assert cli.cmd_flightlog(argparse.Namespace(log=str(log))) == 0


def test_info_prints_metadata(tmp_path, capsys):
    cli.cmd_compile(_compile_ns(DEMO, tmp_path))
    j = tmp_path / "four_drone_demo.skyforge.json"
    capsys.readouterr()                                   # drop compile output
    rc = cli.cmd_info(argparse.Namespace(show=str(j)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Drones" in out and "Duration" in out and "Validation" in out
