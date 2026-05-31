"""
Tests for the deployment/connection profile (show.connection).

The load-bearing guarantee: with no fleet file, the profile is byte-for-byte the
historical SITL configuration. Pure stdlib — no mavsdk needed.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show import config
from show.connection import (
    load_profile, sitl_default_conn, SKYFORGE_FLEET_ENV, GCS_ENV, build_fleet_manifest,
    reconcile_commander_fleet_size, validate_show_fleet_size,
)


def _set_gcs(val):
    old = os.environ.get(GCS_ENV)
    if val is None:
        os.environ.pop(GCS_ENV, None)
    else:
        os.environ[GCS_ENV] = val
    return old


def _restore_gcs(old):
    if old is None:
        os.environ.pop(GCS_ENV, None)
    else:
        os.environ[GCS_ENV] = old


def _fleet_file(data: dict) -> str:
    """Write a temp JSON fleet file and return its path (caller unlinks)."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


# ── Backward-compat: default == exact SITL ────────────────────────────────────

def test_default_is_exact_sitl():
    """No fleet file (and no $SKYFORGE_GCS) → byte-for-byte the historical SITL config."""
    g = _set_gcs(None)
    try:
        prof = load_profile(4, None)
        assert prof.n == 4
        assert prof.spawn_local_server is True
        assert prof.use_gcs_beacon is True
        assert prof.gcs_beacon_mavlink == config.GCS_BEACON_MAVLINK
        assert prof.gcs_beacon_grpc == config.GCS_BEACON_GRPC
        for i in range(4):
            c = prof.conn(i)
            assert c.mavlink_url == f"udpin://0.0.0.0:{config.MAVLINK_BASE + i}"
            assert c.grpc_host == "localhost"
            assert c.grpc_port == config.GRPC_BASE + i
    finally:
        _restore_gcs(g)


def test_env_unset_matches_explicit_none():
    old = os.environ.pop(SKYFORGE_FLEET_ENV, None)
    try:
        assert load_profile(3) == load_profile(3, None)
    finally:
        if old is not None:
            os.environ[SKYFORGE_FLEET_ENV] = old


def test_env_var_is_read_when_path_is_none():
    path = _fleet_file({"drones": [{"mavlink_url": "udp://1.2.3.4:14550"}]})
    old = os.environ.get(SKYFORGE_FLEET_ENV)
    os.environ[SKYFORGE_FLEET_ENV] = path
    try:
        prof = load_profile(1)
        assert prof.conn(0).mavlink_url == "udp://1.2.3.4:14550"
    finally:
        if old is None:
            os.environ.pop(SKYFORGE_FLEET_ENV, None)
        else:
            os.environ[SKYFORGE_FLEET_ENV] = old
        os.unlink(path)


# ── Fleet-file overrides ──────────────────────────────────────────────────────

def test_fleet_file_overrides_urls_and_grpc():
    path = _fleet_file({"drones": [
        {"mavlink_url": "serial:///dev/ttyUSB0:57600"},
        {"mavlink_url": "udp://192.168.1.51:14550", "grpc_port": 50090},
    ]})
    try:
        prof = load_profile(2, path)
        assert prof.n == 2
        assert prof.conn(0).mavlink_url == "serial:///dev/ttyUSB0:57600"
        assert prof.conn(0).grpc_port == config.GRPC_BASE        # default base + 0
        assert prof.conn(1).mavlink_url == "udp://192.168.1.51:14550"
        assert prof.conn(1).grpc_port == 50090                   # explicit override
    finally:
        os.unlink(path)


def test_beacon_and_spawn_flags():
    path = _fleet_file({"use_gcs_beacon": False, "spawn_local_server": False})
    try:
        prof = load_profile(2, path)
        assert prof.use_gcs_beacon is False
        assert prof.spawn_local_server is False
    finally:
        os.unlink(path)


def test_flags_only_keeps_sitl_conns():
    """A file with flags but no 'drones' list keeps the SITL connection defaults."""
    path = _fleet_file({"use_gcs_beacon": False})
    try:
        prof = load_profile(3, path)
        assert prof.n == 3
        for i in range(3):
            assert prof.conn(i) == sitl_default_conn(i)
    finally:
        os.unlink(path)


def test_grpc_host_override():
    path = _fleet_file({"grpc_host": "10.0.0.5",
                        "drones": [{"mavlink_url": "udp://10.0.0.5:14550"}]})
    try:
        assert load_profile(1, path).conn(0).grpc_host == "10.0.0.5"
    finally:
        os.unlink(path)


def test_grpc_base_override():
    path = _fleet_file({"grpc_base": 60000,
                        "drones": [{"mavlink_url": "udp://x:1"},
                                   {"mavlink_url": "udp://y:2"}]})
    try:
        prof = load_profile(2, path)
        assert prof.conn(0).grpc_port == 60000
        assert prof.conn(1).grpc_port == 60001
    finally:
        os.unlink(path)


def test_n_reconcile_uses_drones_length():
    """With a 'drones' list, the file's length is the fleet size — caller reconciles."""
    path = _fleet_file({"drones": [{"mavlink_url": "udp://a:1"},
                                   {"mavlink_url": "udp://b:2"},
                                   {"mavlink_url": "udp://c:3"}]})
    try:
        assert load_profile(1, path).n == 3   # caller said 1; file lists 3
    finally:
        os.unlink(path)


# ── Malformed input ───────────────────────────────────────────────────────────

def _expect_valueerror(path):
    try:
        load_profile(2, path)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_missing_file_raises_valueerror():
    _expect_valueerror("/nonexistent/dir/fleet.json")


def test_malformed_json_raises_valueerror():
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write("{not valid json")
    try:
        _expect_valueerror(path)
    finally:
        os.unlink(path)


def test_drone_without_mavlink_url_raises():
    path = _fleet_file({"drones": [{"grpc_port": 50051}]})   # no mavlink_url
    try:
        _expect_valueerror(path)
    finally:
        os.unlink(path)


# ── Remote-host footgun warning ───────────────────────────────────────────────

def test_remote_grpc_host_warns_when_spawn_local():
    """A remote grpc_host with spawn_local_server=true (default) can't work — the
    local server spawns on localhost while the System connects to the remote host."""
    path = _fleet_file({"drones": [
        {"mavlink_url": "udp://10.0.0.5:14550", "grpc_host": "10.0.0.5"}]})
    try:
        prof = load_profile(1, path)
        assert any("remote" in w for w in prof.warnings)
    finally:
        os.unlink(path)


def test_default_has_no_warnings():
    assert load_profile(4, None).warnings == ()


def test_remote_host_with_spawn_false_no_warning():
    path = _fleet_file({"spawn_local_server": False, "drones": [
        {"mavlink_url": "udp://10.0.0.5:14550", "grpc_host": "10.0.0.5"}]})
    try:
        assert load_profile(1, path).warnings == ()   # externally managed → fine
    finally:
        os.unlink(path)


# ── Fleet-size reconciliation helpers ─────────────────────────────────────────

def test_reconcile_commander_adopts_profile_count():
    path = _fleet_file({"drones": [{"mavlink_url": f"udp://h:{i}"} for i in range(3)]})
    try:
        prof = load_profile(1, path)
        n, msg = reconcile_commander_fleet_size(prof, 1)
        assert n == 3 and msg is not None
    finally:
        os.unlink(path)


def test_reconcile_commander_no_change_when_equal():
    n, msg = reconcile_commander_fleet_size(load_profile(4, None), 4)
    assert n == 4 and msg is None


def test_validate_show_fails_loud_when_too_few():
    path = _fleet_file({"drones": [{"mavlink_url": "udp://h:1"}]})   # 1 drone
    try:
        ok, msg = validate_show_fleet_size(load_profile(1, path), 4)   # show needs 4
        assert ok is False and "Aborting" in msg
    finally:
        os.unlink(path)


def test_validate_show_warns_when_more():
    path = _fleet_file({"drones": [{"mavlink_url": f"udp://h:{i}"} for i in range(6)]})
    try:
        ok, msg = validate_show_fleet_size(load_profile(1, path), 4)
        assert ok is True and msg is not None
    finally:
        os.unlink(path)


def test_validate_show_exact_no_message():
    g = _set_gcs(None)
    try:
        ok, msg = validate_show_fleet_size(load_profile(4, None), 4)
        assert ok is True and msg is None
    finally:
        _restore_gcs(g)


# ── GCS mode ($SKYFORGE_GCS) — QGroundControl integration knob ────────────────

def test_gcs_env_qgc_disables_beacon():
    g = _set_gcs("qgc")
    try:
        assert load_profile(4, None).use_gcs_beacon is False   # no fleet file needed
    finally:
        _restore_gcs(g)


def test_gcs_env_none_disables_beacon():
    g = _set_gcs("none")
    try:
        assert load_profile(4, None).use_gcs_beacon is False
    finally:
        _restore_gcs(g)


def test_gcs_env_beacon_keeps_beacon():
    g = _set_gcs("beacon")
    try:
        prof = load_profile(4, None)
        assert prof.use_gcs_beacon is True and prof.warnings == ()
    finally:
        _restore_gcs(g)


def test_gcs_fleet_file_mode():
    g = _set_gcs(None)
    path = _fleet_file({"gcs": "qgc"})
    try:
        assert load_profile(4, path).use_gcs_beacon is False
    finally:
        _restore_gcs(g)
        os.unlink(path)


def test_gcs_env_overrides_fleet_use_gcs_beacon():
    """$SKYFORGE_GCS=qgc beats a fleet file that says use_gcs_beacon:true."""
    g = _set_gcs("qgc")
    path = _fleet_file({"use_gcs_beacon": True})
    try:
        assert load_profile(4, path).use_gcs_beacon is False
    finally:
        _restore_gcs(g)
        os.unlink(path)


def test_gcs_unknown_mode_warns_and_keeps_beacon():
    g = _set_gcs("nonsense")
    try:
        prof = load_profile(4, None)
        assert prof.use_gcs_beacon is True
        assert any("GCS mode" in w for w in prof.warnings)
    finally:
        _restore_gcs(g)


# ── Provisioning manifest (id ↔ sys_id ↔ slot ↔ trajectory) ───────────────────

def test_manifest_fields_parsed_per_drone():
    path = _fleet_file({"drones": [
        {"mavlink_url": "udp://a:1", "sys_id": 11, "home_ned": [2.0, 4.0, 6.0],
         "slot": 7, "trajectory_file": "show.drone000.skyforge.json"}]})
    try:
        c = load_profile(1, path).conn(0)
        assert c.sys_id == 11 and c.slot == 7
        assert c.home_ned == (2.0, 4.0, 6.0)
        assert c.trajectory_file == "show.drone000.skyforge.json"
    finally:
        os.unlink(path)


def test_default_conn_has_no_manifest_fields():
    c = sitl_default_conn(0)
    assert c.sys_id is None and c.home_ned is None and c.slot is None and c.trajectory_file is None


def test_build_fleet_manifest_assigns_sysid_slot_traj():
    m = build_fleet_manifest(
        3,
        mavlink_urls=[f"udp://10.0.0.{i}:14550" for i in range(3)],
        home_ned_list=[(0.0, 2.0 * i) for i in range(3)],
        trajectory_files=[f"show.drone{i:03d}.skyforge.json" for i in range(3)],
    )
    assert [d["sys_id"] for d in m["drones"]] == [1, 2, 3]   # PX4 MAV_SYS_ID = id+1
    assert [d["slot"] for d in m["drones"]] == [0, 1, 2]
    assert m["drones"][2]["trajectory_file"] == "show.drone002.skyforge.json"
    assert m["use_gcs_beacon"] is False and m["spawn_local_server"] is False


def test_build_fleet_manifest_round_trips_through_load_profile():
    g = _set_gcs(None)
    m = build_fleet_manifest(2, mavlink_urls=["udp://a:1", "udp://b:2"],
                             home_ned_list=[(0.0, 0.0), (0.0, 3.0)])
    path = _fleet_file(m)
    try:
        prof = load_profile(2, path)
        assert prof.n == 2
        assert prof.conn(1).sys_id == 2 and prof.conn(1).home_ned == (0.0, 3.0)
        assert prof.use_gcs_beacon is False
    finally:
        _restore_gcs(g)
        os.unlink(path)
