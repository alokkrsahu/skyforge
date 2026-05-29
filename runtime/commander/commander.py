"""
FleetCommander: high-level drone fleet control API.

Each public method is an async coroutine returning a str status message.
Methods are intentionally MCP-tool-ready: typed params, docstrings, no
side-channel dependencies beyond DynamicRuntime.
"""
import os
import sys
import time
from typing import Union

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from compiler.formations import get_formation

from .dynamic_adapter import DynamicRuntime


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
        for i in range(rt.n_drones):
            hn, he = rt.home_ned[i]
            rt.hold_pos[i] = (hn, he, -altitude_m)
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

        if len(spec) == 1 and spec.isalpha():
            spec = f"text:{spec.upper()}"

        try:
            offsets = get_formation(spec, rt.n_drones)
        except ValueError as e:
            return f"Error: {e}"

        cx, cy  = rt.formation_center
        end_pos = {i: (cx + dN, cy + dE, -rt.alt_m) for i, (dN, dE) in enumerate(offsets)}
        rt.start_transition(end_pos, transition_s)
        return f"Transitioning to {spec!r} over {transition_s:.1f} s"

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
