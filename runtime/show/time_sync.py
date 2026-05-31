"""
Shared-T0 helpers — begin a show/move at one instant across independent controllers.

A live commander on a single host already shares one process clock (every drone reads
the same ``Transition.start_time``), so intra-host moves are coherent. This module adds
the missing piece for SYNCHRONIZED starts across hosts / on-board agents: map an absolute
wall-clock (UNIX/GPS) epoch to the local ``time.monotonic()`` clock, and an opt-in knob
(``SKYFORGE_T0_EPOCH``) to pin the show start to that epoch.

DEFERRED (hardware): a real GPS/PPS time source replaces ``time.time()`` here, and adds
sub-millisecond drift compensation. The software contract below is unchanged by that.
"""
from __future__ import annotations

import os
import time


def monotonic_for_epoch(
    epoch_s: float, *, now_wall: float | None = None, now_mono: float | None = None,
) -> float:
    """Return the local ``time.monotonic()`` value corresponding to absolute UNIX
    ``epoch_s``. ``now_wall``/``now_mono`` are injectable for testing; by default they
    are sampled together so the mapping reflects the current wall↔monotonic offset."""
    nw = time.time()      if now_wall is None else now_wall
    nm = time.monotonic() if now_mono is None else now_mono
    return nm + (epoch_s - nw)


def resolve_t0_epoch(env: str = "SKYFORGE_T0_EPOCH") -> float | None:
    """Monotonic deadline for the absolute UNIX epoch in ``$SKYFORGE_T0_EPOCH``
    (seconds), or ``None`` if unset/blank. Lets a fleet (or several ground hosts) start
    the same instant by exporting the same epoch."""
    raw = os.environ.get(env, "").strip()
    if not raw:
        return None
    return monotonic_for_epoch(float(raw))
