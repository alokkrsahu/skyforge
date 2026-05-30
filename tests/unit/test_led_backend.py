"""
Tests for the pluggable LED backend (show.led_backend).

Covers the factory selection, the StubLed no-op (no subprocess), and pins the
VERBATIM-moved Gazebo backends (4 visual_config / 4 light_config calls with the
expected target names) so the verified SITL protos don't drift. No mavsdk needed;
`gz` subprocess spawns are monkeypatched, so no Gazebo is required either.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show.led_backend import (
    make_led_backend, GazeboVisualLed, GazeboPointLightLed, StubLed, LED_BACKEND_ENV,
)


# ── env helpers (no fixtures, matching the existing async-test style) ──────────

def _set_backend(val):
    old = os.environ.get(LED_BACKEND_ENV)
    if val is None:
        os.environ.pop(LED_BACKEND_ENV, None)
    else:
        os.environ[LED_BACKEND_ENV] = val
    return old


def _restore_backend(old):
    if old is None:
        os.environ.pop(LED_BACKEND_ENV, None)
    else:
        os.environ[LED_BACKEND_ENV] = old


# ── Factory selection ─────────────────────────────────────────────────────────

def test_factory_default_gazebo_per_mode():
    old = _set_backend(None)   # unset → default "gazebo"
    try:
        assert isinstance(make_led_backend("player"), GazeboVisualLed)
        assert isinstance(make_led_backend("commander"), GazeboPointLightLed)
    finally:
        _restore_backend(old)


def test_factory_stub_for_both_modes():
    old = _set_backend("stub")
    try:
        assert isinstance(make_led_backend("player"), StubLed)
        assert isinstance(make_led_backend("commander"), StubLed)
    finally:
        _restore_backend(old)


def test_factory_unknown_falls_back_to_gazebo():
    old = _set_backend("bogus-driver")
    try:
        # Must not raise — an LED setting should never crash a show.
        assert isinstance(make_led_backend("player"), GazeboVisualLed)
        assert isinstance(make_led_backend("commander"), GazeboPointLightLed)
    finally:
        _restore_backend(old)


# ── StubLed is a true no-op (the flight loop must never block on LED I/O) ──────

def test_stub_set_led_spawns_no_subprocess():
    orig = asyncio.create_subprocess_exec
    calls = []

    async def _spy(*a, **k):
        calls.append(a)
        raise AssertionError("StubLed must not spawn a subprocess")

    asyncio.create_subprocess_exec = _spy
    try:
        asyncio.run(StubLed().set_led(0, 1.0, 0.0, 0.0))
        assert calls == []
    finally:
        asyncio.create_subprocess_exec = orig


# ── Gazebo backends: verbatim-move regression locks ───────────────────────────

class _FakeProc:
    async def wait(self):
        return 0


def _capture_gz_calls(coro_factory):
    """Run an LED coroutine with create_subprocess_exec monkeypatched; return the
    list of arg-tuples it would have spawned."""
    orig = asyncio.create_subprocess_exec
    cmds = []

    async def _spy(*a, **k):
        cmds.append(a)
        return _FakeProc()

    asyncio.create_subprocess_exec = _spy
    try:
        asyncio.run(coro_factory())
    finally:
        asyncio.create_subprocess_exec = orig
    return cmds


def test_gazebo_visual_issues_four_visual_config_calls():
    cmds = _capture_gz_calls(lambda: GazeboVisualLed().set_led(0, 1.0, 0.0, 0.0))
    assert len(cmds) == 4
    for a in cmds:
        assert "/world/default/visual_config" in a
        assert "gz.msgs.Visual" in a
    joined = " ".join(" ".join(map(str, a)) for a in cmds)
    for vis in ("5010_motor_base_0", "5010_motor_base_1",
                "5010_motor_base_2", "5010_motor_base_3"):
        assert vis in joined


def test_gazebo_pointlight_issues_four_light_config_calls():
    cmds = _capture_gz_calls(lambda: GazeboPointLightLed().set_led(0, 1.0, 0.0, 0.0))
    assert len(cmds) == 4
    for a in cmds:
        assert "/world/default/light_config" in a
        assert "gz.msgs.Light" in a
    joined = " ".join(" ".join(map(str, a)) for a in cmds)
    for light in ("light_front_left", "light_front_right",
                  "light_rear_left", "light_rear_right"):
        assert light in joined
