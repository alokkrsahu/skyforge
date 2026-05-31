"""
Tests for the hardware LED driver template (HardwareLedDriver) + factory selection.
No Gazebo, no MAVSDK — the driver delegates to an injected async sender we record.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show.led_backend import HardwareLedDriver, make_led_backend, LED_BACKEND_ENV


def test_factory_selects_hardware_driver():
    old = os.environ.get(LED_BACKEND_ENV)
    try:
        os.environ[LED_BACKEND_ENV] = "hardware"
        assert isinstance(make_led_backend("commander"), HardwareLedDriver)
        assert isinstance(make_led_backend("player"), HardwareLedDriver)
    finally:
        if old is None: os.environ.pop(LED_BACKEND_ENV, None)
        else: os.environ[LED_BACKEND_ENV] = old


def test_driver_delegates_to_sender():
    calls = []
    async def sender(drone_id, r, g, b):
        calls.append((drone_id, r, g, b))
    drv = HardwareLedDriver(sender=sender)
    asyncio.run(drv.set_led(3, 1.0, 0.0, 0.5))
    assert calls == [(3, 1.0, 0.0, 0.5)]


def test_driver_without_sender_is_safe_noop():
    drv = HardwareLedDriver()                     # no driver wired
    asyncio.run(drv.set_led(0, 0.0, 0.8, 0.0))    # must not raise


def test_subclass_emit_is_used():
    seen = []
    class _Bus(HardwareLedDriver):
        async def _emit(self, drone_id, r, g, b):
            seen.append((drone_id, r, g, b))
    asyncio.run(_Bus().set_led(7, 0.1, 0.2, 0.3))
    assert seen == [(7, 0.1, 0.2, 0.3)]
