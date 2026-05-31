"""
Battery / energy budgeting for a compiled show.

A show that runs longer than the airframes' endurance — or that flies them hard — will
drop drones out of the sky on low battery mid-performance. This estimates per-drone
battery usage from the planned trajectories (hover-dominated for slow show flight, plus a
small distance term) and flags whether the show lands with a safe reserve. Approximate by
design: it's a planning guardrail, not a power model. Tune `EnergyModel` to your airframe.

DEFERRED (hardware): a measured power curve (current vs. speed/throttle) + real battery
telemetry to calibrate the model and reconcile against actual consumption.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sampling import sample_positions


@dataclass
class EnergyModel:
    endurance_hover_s: float = 600.0    # full-charge hover time (~10 min, typical show drone)
    energy_per_m_frac: float = 0.0008   # extra battery fraction spent per metre travelled
    reserve_frac:      float = 0.20     # must land with >= this fraction remaining


@dataclass
class EnergyReport:
    duration_s:     float
    max_used_frac:  float
    worst_drone:    int
    per_drone_used: list
    fits:           bool


def estimate_energy(show, model: EnergyModel | None = None, sample_hz: float = 2.0) -> EnergyReport:
    """Estimate each drone's battery usage as ``duration/endurance + distance*per_m`` and
    report whether the worst drone still clears the reserve."""
    model = model or EnergyModel()
    dur   = show.metadata.duration_s
    times = np.linspace(0.0, dur, max(2, int(dur * sample_hz)))
    P     = sample_positions(show.trajectories, times)          # (n, T, 3)

    used = []
    for i in range(P.shape[0]):
        dist = float(np.linalg.norm(np.diff(P[i], axis=0), axis=1).sum())
        used.append(dur / model.endurance_hover_s + dist * model.energy_per_m_frac)

    mx    = max(used) if used else 0.0
    worst = used.index(mx) if used else -1
    return EnergyReport(
        duration_s=dur, max_used_frac=mx, worst_drone=worst,
        per_drone_used=used, fits=(mx <= 1.0 - model.reserve_frac),
    )
