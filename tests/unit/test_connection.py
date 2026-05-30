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
    load_profile, sitl_default_conn, SKYFORGE_FLEET_ENV,
)


def _fleet_file(data: dict) -> str:
    """Write a temp JSON fleet file and return its path (caller unlinks)."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


# ── Backward-compat: default == exact SITL ────────────────────────────────────

def test_default_is_exact_sitl():
    """No fleet file → byte-for-byte the historical SITL configuration."""
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
