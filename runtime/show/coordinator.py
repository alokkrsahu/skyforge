"""Show sequencer — advances acts, builds Bézier segments, holds shared state."""
import asyncio
from typing import Dict, List, Optional, Tuple

from .barrier import ShowBarrier
from .bezier import BezierSegment
from .config import CONTROL_DT, N_DRONES, SHOW_SCRIPT
from .formations import formation_targets


class ShowCoordinator:
    def __init__(self, barrier: ShowBarrier):
        self.barrier = barrier

        # Shared state written by drone controllers each tick
        self.current_positions: Dict[int, Tuple[float, float, float]] = {}

        # Shared state read by drone controllers each tick
        self.current_targets:   Dict[int, Tuple[float, float, float]] = {}
        self.current_bezier:    Dict[int, Optional[BezierSegment]]   = {i: None for i in range(N_DRONES)}

        # Monotonic counter; drone controllers detect segment changes by comparing
        self.segment_id: int = 0

        self.show_complete: bool = False

    def _set_act(self, formation: str, center_ne: Tuple[float, float], transition_s: float):
        """Compute new targets + Bézier segments for all drones."""
        targets = formation_targets(formation, center_ne)
        for i, tgt in enumerate(targets):
            src = self.current_positions.get(i, tgt)
            src_ne = (src[0], src[1])
            tgt_ne = (tgt[0], tgt[1])
            self.current_bezier[i]  = BezierSegment(src_ne, tgt_ne, transition_s)
            self.current_targets[i] = tgt
        self.barrier.set_targets({i: targets[i] for i in range(N_DRONES)})
        self.segment_id += 1

    def _hold(self):
        """Signal drone controllers to hold current targets (no active path)."""
        for i in range(N_DRONES):
            self.current_bezier[i] = None
        self.segment_id += 1

    async def coordinator_loop(self):
        print("[coordinator] Waiting for all drones to report position...")
        # Wait until all 4 drones have reported at least one position
        while len(self.current_positions) < N_DRONES:
            await asyncio.sleep(CONTROL_DT)

        for act_num, (formation, center_ne, transition_s, hold_s) in enumerate(SHOW_SCRIPT, 1):
            print(f"[coordinator] Act {act_num}: {formation} @ center{center_ne}  "
                  f"(transition {transition_s}s, hold {hold_s}s)")

            # Start transition
            self.barrier.reset()
            self._set_act(formation, center_ne, transition_s)

            # Wait for convergence (timeout = 2× transition time)
            converged = await self.barrier.wait(timeout_s=transition_s * 2.0)
            if not converged:
                print(f"[coordinator] WARNING: Act {act_num} timed out — forcing advance")

            # Hold formation
            self._hold()
            await asyncio.sleep(hold_s)

            # Tick the barrier checker during hold so it stays current
            # (drone controllers keep reporting positions during hold)

        print("[coordinator] Show complete — signalling drones to land")
        self.show_complete = True

    async def tick(self):
        """Called by drone controllers each tick to trigger barrier check."""
        self.barrier.check_and_signal()
