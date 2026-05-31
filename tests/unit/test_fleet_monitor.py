"""
Tests for fleet observability: health aggregation, auto-abort decision, black-box log.
Pure module — no MAVSDK.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show.fleet_monitor import (
    DroneHealth, AbortPolicy, summarize, should_auto_abort, BlackBox,
)


def test_summarize_counts_lost_and_worst():
    healths = [
        DroneHealth(0, age_s=0.1, pos_error_m=0.5, battery_frac=0.9),
        DroneHealth(1, age_s=0.1, pos_error_m=2.0, battery_frac=0.4),
        DroneHealth(2, age_s=9.0, pos_error_m=0.0, battery_frac=0.8),   # stale → lost
    ]
    s = summarize(healths, n_total=3, stale_age_s=2.0)
    assert s.n_seen == 2 and s.n_lost == 1
    assert s.min_battery_frac == 0.4              # worst among SEEN
    assert s.max_pos_error_m == 2.0
    assert any("lost" in a for a in s.anomalies)


def test_auto_abort_on_lost_fraction():
    s = summarize([DroneHealth(0, age_s=9.0)], n_total=4)   # 4 lost of 4? no: 1 health, 3 missing
    # n_total=4, healths has 1 (stale) → n_seen 0, n_lost 4 → 100% > 25%
    fire, why = should_auto_abort(s, AbortPolicy())
    assert fire and "lost" in why


def test_auto_abort_on_low_battery():
    s = summarize([DroneHealth(0, age_s=0.1, battery_frac=0.05)], n_total=1)
    fire, why = should_auto_abort(s, AbortPolicy(min_battery_frac=0.10))
    assert fire and "battery" in why


def test_auto_abort_on_tracking_error():
    s = summarize([DroneHealth(0, age_s=0.1, pos_error_m=8.0)], n_total=1)
    fire, why = should_auto_abort(s, AbortPolicy(max_pos_error_m=5.0))
    assert fire and "tracking error" in why


def test_no_abort_when_healthy():
    healths = [DroneHealth(i, age_s=0.1, pos_error_m=0.3, battery_frac=0.9) for i in range(4)]
    s = summarize(healths, n_total=4)
    fire, why = should_auto_abort(s, AbortPolicy())
    assert not fire and why == ""


def test_blackbox_appends_jsonl(tmp_path):
    p = tmp_path / "flight.jsonl"
    bb = BlackBox(str(p))
    bb.record({"t": 0.0, "n_seen": 4})
    bb.record({"t": 0.1, "n_seen": 3})
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["n_seen"] == 3
