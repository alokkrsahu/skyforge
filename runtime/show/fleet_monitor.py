"""
Fleet observability — aggregate health, anomaly → auto-abort, and a black-box log.

QGroundControl watches a handful of vehicles; a show needs a one-glance fleet summary
(how many seen/lost, worst battery, worst tracking error) plus an automatic safety net
that triggers a fleet abort when things go wrong, and a flight recorder for post-mortem.

This module is PURE (no MAVSDK / no runtime import) so it is fully unit-testable; the
live loop in the commander builds `DroneHealth` snapshots from its caches and calls these.
Battery/GPS fields are optional — populated when telemetry is subscribed, else None.
DEFERRED (hardware): battery/GPS subscription wiring + a real ground dashboard UI.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class DroneHealth:
    drone_id:     int
    armed:        bool = True
    age_s:        float = 0.0           # seconds since last telemetry
    pos_error_m:  float | None = None   # |actual - commanded target|
    battery_frac: float | None = None   # 0..1, if subscribed
    gps_ok:       bool = True


@dataclass
class FleetSummary:
    n_total:          int
    n_seen:           int
    n_lost:           int
    min_battery_frac: float | None
    max_pos_error_m:  float | None
    anomalies:        list = field(default_factory=list)


@dataclass
class AbortPolicy:
    max_lost_frac:    float = 0.25      # > 25% of the fleet lost → abort
    min_battery_frac: float = 0.10      # any drone below 10% → abort
    max_pos_error_m:  float = 5.0       # any drone > 5 m off its target → abort


def summarize(healths: list[DroneHealth], n_total: int, *, stale_age_s: float = 2.0) -> FleetSummary:
    """Aggregate per-drone health into a fleet-level snapshot."""
    seen = [h for h in healths if h.age_s <= stale_age_s]
    bats = [h.battery_frac for h in seen if h.battery_frac is not None]
    errs = [h.pos_error_m for h in seen if h.pos_error_m is not None]
    anomalies: list[str] = []
    n_lost = n_total - len(seen)
    if n_lost:
        anomalies.append(f"{n_lost} drone(s) stale/lost")
    if any(not h.gps_ok for h in seen):
        anomalies.append("GPS not OK on some drones")
    return FleetSummary(
        n_total=n_total, n_seen=len(seen), n_lost=n_lost,
        min_battery_frac=min(bats) if bats else None,
        max_pos_error_m=max(errs) if errs else None,
        anomalies=anomalies,
    )


def should_auto_abort(summary: FleetSummary, policy: AbortPolicy) -> tuple[bool, str]:
    """Decide whether the monitored state warrants an automatic fleet abort."""
    reasons: list[str] = []
    if summary.n_total and summary.n_lost / summary.n_total > policy.max_lost_frac:
        reasons.append(f"{summary.n_lost}/{summary.n_total} drones lost")
    if summary.min_battery_frac is not None and summary.min_battery_frac < policy.min_battery_frac:
        reasons.append(f"battery {summary.min_battery_frac:.0%} < {policy.min_battery_frac:.0%}")
    if summary.max_pos_error_m is not None and summary.max_pos_error_m > policy.max_pos_error_m:
        reasons.append(f"tracking error {summary.max_pos_error_m:.1f} m > {policy.max_pos_error_m:.0f} m")
    return (bool(reasons), "; ".join(reasons))


class BlackBox:
    """Append-only JSONL flight recorder (one record per line) for post-flight review."""
    def __init__(self, path: str):
        self.path = path

    def record(self, obj: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(obj) + "\n")
