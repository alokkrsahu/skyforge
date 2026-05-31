"""
Tests for the fleet broadcast channel + link-loss fail-safe ladder. Pure (file-backed).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

import pytest

from show.broadcast import FleetBroadcast, link_loss_action, COMMANDS


def test_publish_and_latest(tmp_path):
    ch = FleetBroadcast(str(tmp_path / "cmd.json"))
    assert ch.latest() is None                            # nothing yet
    r = ch.publish("start", epoch=123.0)
    assert r["command"] == "start" and r["epoch"] == 123.0
    assert ch.latest()["command"] == "start"


def test_seq_increments_so_receivers_detect_new(tmp_path):
    ch = FleetBroadcast(str(tmp_path / "cmd.json"))
    s1 = ch.publish("start")["seq"]
    s2 = ch.publish("hold")["seq"]
    s3 = ch.publish("abort")["seq"]
    assert s1 < s2 < s3
    assert ch.latest()["command"] == "abort"


def test_unknown_command_rejected(tmp_path):
    ch = FleetBroadcast(str(tmp_path / "cmd.json"))
    with pytest.raises(ValueError):
        ch.publish("explode")
    assert set(COMMANDS) == {"start", "abort", "hold", "rtl"}


def test_link_loss_ladder():
    assert link_loss_action(1.0) is None                  # brief gap → ride it
    assert link_loss_action(5.0) == "hold"                # quiet a while → hold
    assert link_loss_action(20.0) == "land"               # prolonged → land
    # tunable thresholds
    assert link_loss_action(3.0, hold_grace_s=1.0, land_after_s=2.5) == "land"


def test_cross_reader_sees_publish(tmp_path):
    p = str(tmp_path / "cmd.json")
    FleetBroadcast(p).publish("rtl")
    assert FleetBroadcast(p).latest()["command"] == "rtl"  # a second reader (other process)
