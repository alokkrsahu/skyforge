"""
FleetCommander: high-level drone fleet control API.

Each public method is an async coroutine returning a str status message.
Methods are intentionally MCP-tool-ready: typed params, docstrings, no
side-channel dependencies beyond DynamicRuntime.
"""
import asyncio
import os
import sys
import time
from typing import Union

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from compiler.formations import get_formation, list_formations
from compiler.assignment import assign_nocross

from .dynamic_adapter import DynamicRuntime
from show.config import MIN_SEP_M

# Plan transitions with margin ABOVE the 1.5 m hard floor: the geometric closest
# approach during a move otherwise sits right at MIN_SEP_M, so a ~0.3 m PX4 tracking
# error breaches it (→ the occasional single-drone contact). Scaling formations to
# 3.0 m AND making the assignment keep crossing paths >= 2.5 m yields a worst-case
# ~2.1 m over ALL formation-pair transitions at 16 drones — ~0.6 m of execution
# headroom (verified by brute force). The validator's 1.5 m floor stays the limit.
_PLAN_SCALE_M = MIN_SEP_M + 1.5   # 3.0 m — formation (hold) spacing
_PLAN_CROSS_M = MIN_SEP_M + 1.0   # 2.5 m — min transition clearance the assignment targets
# Robust sizing for live formations: scale off the ~20th-percentile nearest-neighbour
# distance, not the single tightest pair, so a DESIGNED pattern with a few near-touching
# detail points (e.g. a cat's ears/eyes) isn't ballooned out to a huge radius. The few
# sub-spacing feature points rely on assign_nocross + APF (the reactive backstop).
# Uniform patterns (circle/grid) are unaffected (every percentile == the min).
_PLAN_SPACING_PCT = 20.0


_COLOR_NAMES: dict[str, tuple[float, float, float]] = {
    "red":    (1.0, 0.0, 0.0),
    "green":  (0.0, 1.0, 0.0),
    "blue":   (0.0, 0.4, 1.0),
    "white":  (1.0, 1.0, 1.0),
    "off":    (0.0, 0.0, 0.0),
    "orange": (1.0, 0.4, 0.0),
    "purple": (0.6, 0.0, 1.0),
    "cyan":   (0.0, 1.0, 0.8),
    "yellow": (1.0, 0.9, 0.0),
    "pink":   (1.0, 0.0, 0.6),
}


class FleetCommander:
    def __init__(self, runtime: DynamicRuntime):
        self.runtime = runtime

    # ── Flight lifecycle ──────────────────────────────────────────────────────

    async def takeoff(self, altitude_m: float = 5.0) -> str:
        """Arm all drones and ascend to altitude_m metres."""
        rt = self.runtime
        if rt.airborne:
            return f"Already airborne. Use 'alt {altitude_m:.0f}' to change altitude."
        # Reset per-flight state before signalling coroutines
        rt.alt_m      = altitude_m
        rt.abort_flag = False
        rt.airborne   = False
        rt.ready_count = 0
        rt.transition  = None
        # Rise IN PLACE: keep each drone's current parked XY and only set the
        # cruise altitude. Resetting XY to home here made a takeoff AFTER a
        # formation (e.g. circle → land) converge the whole fleet from their
        # spread landed positions onto the tight 2 m home grid at once → pile-up →
        # "Attitude failure (roll)" tumble. PX4's takeoff is vertical, so the drone
        # is already above its landed XY; hold there and let a later `formation`
        # command do the planned (scaled + crossing-free) rearrangement.
        for i in range(rt.n_drones):
            px, py, _ = rt.hold_pos.get(i, (rt.home_ned[i][0], rt.home_ned[i][1], -altitude_m))
            rt.hold_pos[i] = (px, py, -altitude_m)
        rt.flight_cycle += 1   # wakes drone coroutines waiting for next cycle
        return f"Taking off to {altitude_m:.1f} m — waiting for all drones..."

    async def land(self, stagger: bool = True) -> str:
        """Descend and disarm all drones; stagger=True spaces landings by drone_id × 1.5 s."""
        self.runtime.abort_flag = not stagger
        self.runtime.airborne   = False
        return "Landing" + (" (staggered)" if stagger else " (immediate)")

    async def abort(self) -> str:
        """Emergency: immediately land all drones without stagger."""
        self.runtime.abort_flag = True
        self.runtime.airborne   = False
        return "ABORT — emergency landing"

    async def rtl(self, transition_s: float = 8.0) -> str:
        """Return-to-launch: fly the whole fleet back to its home XY (at cruise
        altitude) over transition_s, then land. Reuses the planned transition
        (start_transition → crossing-free interpolation) and the existing staggered
        land path, so no in-flight surgery is needed — it's a coordinated, recoverable
        emergency, unlike `abort` (drop where you are) or `land` (down in place)."""
        rt = self.runtime
        if not rt.airborne:
            return "Drones not airborne"
        end_pos = {
            i: (rt.home_ned[i][0], rt.home_ned[i][1], -rt.alt_m)
            for i in range(rt.n_drones)
        }
        rt.start_transition(end_pos, transition_s)

        async def _land_after_return() -> None:
            await asyncio.sleep(transition_s)
            rt.airborne = False   # each drone coroutine then lands (staggered)

        asyncio.create_task(_land_after_return())
        return f"RTL — returning to launch over {transition_s:.1f} s, then landing"

    # ── Formation control ─────────────────────────────────────────────────────

    async def hover(self) -> str:
        """Cancel active transition and hold current positions."""
        rt = self.runtime
        if rt.transition is not None:
            now = time.monotonic()
            for i in range(rt.n_drones):
                rt.hold_pos[i] = rt.target_ned(i, now)
            rt.transition = None
        return "Hovering in place"

    async def formation(self, spec: str, transition_s: float = 6.0) -> str:
        """Move all drones to a named formation.

        spec: 'circle', 'grid', 'line', 'v_shape', 'star', 'spiral',
              'text:A', 'text:HELLO', 'text:HELLO:scale=3',
              'circle:radius_m=8', 'grid:spacing=4',
              single capital letter A–Z (sugar for text:<letter>).
        transition_s: seconds to complete the move (default 10).
        """
        rt = self.runtime
        if not rt.airborne:
            n_ready = rt.current_positions.__len__()
            return (f"Drones not airborne yet ({n_ready} in offboard, waiting for sync). "
                    f"Type 'status' to check, or wait for '[commander] All drones in offboard'.")

        # Single capital letter = sugar for text:<letter> — but NOT if that letter is a real
        # registered formation (e.g. 'v' = the v_shape alias). The REPL guards this before
        # dispatch; the API/web path calls formation() directly, so guard it here too.
        if len(spec) == 1 and spec.isalpha() and spec.lower() not in list_formations():
            spec = f"text:{spec.upper()}"

        try:
            # Scale the formation so neighbours clear the planned separation. Without
            # this the raw fixed-radius formations pack sub-metre at scale.
            offsets = get_formation(spec, rt.n_drones, min_spacing_m=_PLAN_SCALE_M,
                                    spacing_percentile=_PLAN_SPACING_PCT)
        except ValueError as e:
            return f"Error: {e}"

        # Assign drones to slots minimising path crossings (Hungarian + separation
        # repair) from their CURRENT positions, rather than the naive drone-i→slot-i
        # mapping — the latter sends drones straight through each other on a dense
        # pattern change (e.g. text:A → text:B). The crossing check is HORIZONTAL
        # (XY); each drone then flies STRAIGHT to its 3D slot — no climb/cross/descend
        # layering, so there's nothing to vertically reconverge (PX4-trackable). The
        # formation's own dU (a volumetric sculpture) gives per-drone target altitudes;
        # APF (3D) is the reactive backstop, and dU separation only helps.
        cx, cy  = rt.formation_center
        targets = [(cx + dN, cy + dE, dU) for dN, dE, dU in offsets]
        current = []
        for i in range(rt.n_drones):
            p = rt.current_positions.get(i) or rt.hold_pos.get(i, (cx, cy, -rt.alt_m))
            current.append((p[0], p[1]))
        assignment = assign_nocross(current, [(t[0], t[1]) for t in targets], _PLAN_CROSS_M)
        end_pos = {
            # per-drone altitude = cruise altitude + the slot's up offset (dU >= 0)
            i: (targets[assignment[i]][0], targets[assignment[i]][1],
                -rt.alt_m - targets[assignment[i]][2])
            for i in range(rt.n_drones)
        }
        rt.start_transition(end_pos, transition_s)
        kind = "3D sculpture" if any(t[2] > 1e-6 for t in targets) else "scaled + crossing-free"
        return f"Transitioning to {spec!r} over {transition_s:.1f} s ({kind} assignment)"

    async def move(
        self,
        dN: float = 0.0,
        dE: float = 0.0,
        transition_s: float = 5.0,
    ) -> str:
        """Shift the entire formation dN metres north and dE metres east."""
        rt = self.runtime
        if not rt.airborne:
            return "Drones not airborne"
        cx, cy = rt.formation_center
        rt.formation_center = (cx + dN, cy + dE)
        end_pos = {i: (p[0] + dN, p[1] + dE, p[2]) for i, p in rt.hold_pos.items()}
        rt.start_transition(end_pos, transition_s)
        return f"Moving ({dN:+.1f} N, {dE:+.1f} E) over {transition_s:.1f} s"

    async def set_altitude(self, alt_m: float, transition_s: float = 5.0) -> str:
        """Change cruise altitude for all drones."""
        rt = self.runtime
        rt.alt_m = alt_m
        end_pos  = {i: (p[0], p[1], -alt_m) for i, p in rt.hold_pos.items()}
        rt.start_transition(end_pos, transition_s)
        return f"Altitude → {alt_m:.1f} m over {transition_s:.1f} s"

    # ── Appearance ────────────────────────────────────────────────────────────

    async def set_color(
        self,
        r: Union[float, str] = 0.0,
        g: float = 0.8,
        b: float = 0.0,
    ) -> str:
        """Set LED colour for all drones. r can be a name: red/green/blue/white/off/orange/purple/cyan/yellow/pink."""
        if isinstance(r, str):
            named = _COLOR_NAMES.get(r.lower())
            if named is None:
                return f"Unknown colour '{r}'. Known: {', '.join(_COLOR_NAMES)}"
            r, g, b = named
        self.runtime.led_color = (
            max(0.0, min(1.0, float(r))),
            max(0.0, min(1.0, float(g))),
            max(0.0, min(1.0, float(b))),
        )
        return f"LED → ({float(r):.2f}, {float(g):.2f}, {float(b):.2f})"

    # ── Telemetry ─────────────────────────────────────────────────────────────

    async def status(self) -> str:
        """Return current positions, altitude, and transition state of the fleet."""
        rt    = self.runtime
        state = "AIRBORNE" if rt.airborne else "GROUNDED"
        tr_str = ""
        if rt.transition:
            remaining = max(0.0, rt.transition.duration_s - (time.monotonic() - rt.transition.start_time))
            tr_str = f"  (transition {remaining:.1f}s remaining)"
        lines = [
            f"Fleet: {state}{tr_str}",
            f"  centre ({rt.formation_center[0]:.1f} N, {rt.formation_center[1]:.1f} E)  "
            f"alt={rt.alt_m:.1f} m  LED={rt.led_color}",
        ]
        for i in range(rt.n_drones):
            pos = rt.current_positions.get(i)
            if pos:
                lines.append(f"  drone {i:2d}  N={pos[0]:6.1f}  E={pos[1]:6.1f}  alt={-pos[2]:.1f}m")
            else:
                lines.append(f"  drone {i:2d}  (no telemetry yet)")
        return "\n".join(lines)

    async def snapshot(self) -> dict:
        """Structured live state for a UI — the read seam (vs the human-text `status`).
        Reads ONLY DynamicRuntime (the 10 Hz telemetry cache) and uses peek_target (no
        side effects), so it is safe to run on the control loop. Never touches a MAVSDK
        stream (the no-wait_for invariant). Distances in metres; pos/vel are global NED
        (D down, negative = up); `stale` = no telemetry > 2 s while airborne."""
        rt  = self.runtime
        now = time.monotonic()
        tr  = None
        if rt.transition:
            remaining = max(0.0, rt.transition.duration_s - (now - rt.transition.start_time))
            tr = {"remaining_s": round(remaining, 2), "duration_s": rt.transition.duration_s}
        drones = []
        for i in range(rt.n_drones):
            ts    = rt.position_timestamps.get(i)
            stale = ts is None or (rt.airborne and now - ts > 2.0)
            drones.append({
                "id":     i,
                "pos":    rt.current_positions.get(i),
                "vel":    rt.current_velocities.get(i),
                "target": rt.peek_target(i, now),
                "stale":  stale,
            })
        return {
            "airborne": rt.airborne, "abort": rt.abort_flag, "alt_m": rt.alt_m,
            "led": list(rt.led_color), "center": list(rt.formation_center),
            "flight_cycle": rt.flight_cycle, "ready": [rt.ready_count, rt.ready_target],
            "transition": tr, "drones": drones,
        }
