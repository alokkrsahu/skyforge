"""
Tests for PX4 failsafe/geofence provisioning. The MAVSDK param plugin is stubbed
with a recorder; we assert the right params are pushed via the right setter (int vs
float), that config overrides apply, and that a per-param failure is skipped (not fatal).
No hardware.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show.failsafe_provisioning import FailsafeConfig, provision_failsafes


class _Param:
    def __init__(self, fail_on=None):
        self.ints: dict[str, int] = {}
        self.floats: dict[str, float] = {}
        self._fail_on = fail_on
    async def set_param_int(self, name, value):
        if name == self._fail_on: raise RuntimeError("no such param")
        self.ints[name] = value
    async def set_param_float(self, name, value):
        if name == self._fail_on: raise RuntimeError("no such param")
        self.floats[name] = value


class _Drone:
    def __init__(self, fail_on=None):
        self.param = _Param(fail_on)


def test_default_config_pushes_full_set():
    d = _Drone()
    applied = asyncio.run(provision_failsafes(d))
    # geofence + RTL + battery + offboard/RC-loss all set
    assert {"GF_ACTION", "GF_MAX_HOR_DIST", "RTL_RETURN_ALT", "BAT_LOW_THR",
            "COM_LOW_BAT_ACT", "COM_OBL_ACT", "COM_RC_IN_MODE"} <= set(applied)
    assert d.param.ints["GF_ACTION"] == 2                 # Hold
    assert d.param.floats["GF_MAX_HOR_DIST"] == 100.0
    assert d.param.ints["COM_RC_IN_MODE"] == 1            # joystick / no-RC (SITL)


def test_int_vs_float_routing():
    d = _Drone()
    asyncio.run(provision_failsafes(d))
    assert "BAT_LOW_THR" in d.param.floats and "BAT_LOW_THR" not in d.param.ints
    assert "GF_ACTION"  in d.param.ints  and "GF_ACTION"  not in d.param.floats


def test_config_overrides_apply():
    d = _Drone()
    cfg = FailsafeConfig(geofence_radius_m=250.0, geofence_action=3, rc_in_mode=0)
    asyncio.run(provision_failsafes(d, cfg))
    assert d.param.floats["GF_MAX_HOR_DIST"] == 250.0
    assert d.param.ints["GF_ACTION"] == 3                 # RTL
    assert d.param.ints["COM_RC_IN_MODE"] == 0            # real RC


def test_missing_param_is_skipped_not_fatal():
    d = _Drone(fail_on="COM_OBL_ACT")                     # simulate older firmware
    applied = asyncio.run(provision_failsafes(d))
    assert "COM_OBL_ACT" not in applied                   # skipped
    assert "GF_ACTION" in applied                         # the rest still applied


def test_rcl_except_bit():
    d = _Drone()
    asyncio.run(provision_failsafes(d, FailsafeConfig(rcl_except_offboard=True)))
    assert d.param.ints["COM_RCL_EXCEPT"] == 4
    d2 = _Drone()
    asyncio.run(provision_failsafes(d2, FailsafeConfig(rcl_except_offboard=False)))
    assert d2.param.ints["COM_RCL_EXCEPT"] == 0


def test_from_dict_and_env(tmp_path):
    cfg = FailsafeConfig.from_dict({"rtl_return_alt_m": 42.0, "unknown_key": 1})
    assert cfg.rtl_return_alt_m == 42.0                   # known key applied, unknown ignored
    p = tmp_path / "fs.json"
    p.write_text(json.dumps({"geofence_radius_m": 75.0}))
    old = os.environ.get("SKYFORGE_FAILSAFE_CONFIG")
    try:
        os.environ["SKYFORGE_FAILSAFE_CONFIG"] = str(p)
        assert FailsafeConfig.from_env().geofence_radius_m == 75.0
        os.environ.pop("SKYFORGE_FAILSAFE_CONFIG")
        assert FailsafeConfig.from_env() is None
    finally:
        if old is not None: os.environ["SKYFORGE_FAILSAFE_CONFIG"] = old
