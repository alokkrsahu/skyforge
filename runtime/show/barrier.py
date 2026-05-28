"""Asyncio barrier: all drones must converge before the show advances."""
import asyncio
import math
from typing import Dict, Optional, Tuple

from .config import BARRIER_THRESHOLD_M, N_DRONES


class ShowBarrier:
    def __init__(self):
        self._positions: Dict[int, Tuple[float, float, float]] = {}
        self._targets:   Dict[int, Tuple[float, float, float]] = {}
        self._event = asyncio.Event()

    def update_position(self, drone_id: int, pos: Tuple[float, float, float]):
        self._positions[drone_id] = pos

    def set_targets(self, targets: Dict[int, Tuple[float, float, float]]):
        self._targets = dict(targets)

    def check_and_signal(self):
        """Call each coordinator tick; fires event when all drones converged."""
        if self._event.is_set():
            return
        if len(self._positions) < N_DRONES or len(self._targets) < N_DRONES:
            return
        for i in range(N_DRONES):
            p = self._positions.get(i)
            t = self._targets.get(i)
            if p is None or t is None:
                return
            if math.dist(p, t) > BARRIER_THRESHOLD_M:
                return
        self._event.set()

    async def wait(self, timeout_s: Optional[float] = None) -> bool:
        """Await convergence; returns True if converged, False if timed out."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    def reset(self):
        self._event.clear()
