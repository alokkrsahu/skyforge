"""
ShowBuilder — high-level compiler API.

Usage:
    builder = ShowBuilder("My Show", drones)
    builder.add_act("diamond", center_ne=(5, 5), transition_s=10, hold_s=5)
    builder.add_led_cue(t=0, color=Color(0, 0.9, 0), drone_ids=[])
    show = builder.compile()
    writer.to_json(show, "my_show.skyforge.json")
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from core.show_format.schema import (
    Color, DroneEnvelope, DroneSpec, EnvelopeSegment,
    LedKeyframe, LedTrack, NominalTrajectory, ReactiveBinding,
    ShowFile, ShowMetadata, Vec3, VenueOrigin,
)
from compiler.assignment import assign
from compiler.trajectory_generator import fit_trajectory

# Formation offsets (dN, dE) from centre — matches drone_simulation/show/config.py
FORMATIONS: dict[str, list[tuple[float, float]]] = {
    "grid":    [(-1.0, -1.0), (-1.0,  1.0), ( 1.0, -1.0), ( 1.0,  1.0)],
    "line":    [( 0.0, -3.0), ( 0.0, -1.0), ( 0.0,  1.0), ( 0.0,  3.0)],
    "diamond": [(-2.0,  0.0), ( 0.0, -2.0), ( 2.0,  0.0), ( 0.0,  2.0)],
    "arrow":   [( 0.0,  0.0), ( 2.0, -2.0), ( 2.0,  2.0), ( 4.0,  0.0)],
}

TAKEOFF_ALT_M = 5.0
SHOW_ALT_M    = 5.0


@dataclass
class _Act:
    formation:    str
    center_ne:    tuple[float, float]
    transition_s: float
    hold_s:       float


@dataclass
class _LedCue:
    t:         float
    color:     Color
    drone_ids: list[int]


class ShowBuilder:
    def __init__(
        self,
        name:    str,
        drones:  list[DroneSpec],
        origin:  VenueOrigin = None,
        author:  str = "",
        venue:   str = "",
    ):
        self._name   = name
        self._drones = drones
        self._n      = len(drones)
        self._origin = origin or VenueOrigin()
        self._author = author
        self._venue  = venue

        self._acts:              list[_Act]            = []
        self._led_cues:          list[_LedCue]         = []
        self._reactive_bindings: list[ReactiveBinding] = []

    # ── Authoring API ─────────────────────────────────────────────────────────

    def add_act(
        self,
        formation:    str,
        center_ne:    tuple[float, float],
        transition_s: float,
        hold_s:       float,
    ) -> "ShowBuilder":
        assert formation in FORMATIONS, f"Unknown formation '{formation}'"
        self._acts.append(_Act(formation, center_ne, transition_s, hold_s))
        return self

    def add_led_cue(
        self,
        t:         float,
        color:     Color,
        drone_ids: list[int] = None,
    ) -> "ShowBuilder":
        self._led_cues.append(_LedCue(t, color, drone_ids or []))
        return self

    def add_reactive_binding(
        self,
        input_source: str,
        primitive:    str,
        parameters:   dict,
        t_start:      float,
        t_end:        float,
        drone_ids:    list[int] = None,
    ) -> "ShowBuilder":
        self._reactive_bindings.append(ReactiveBinding(
            input_source = input_source,
            primitive    = primitive,
            parameters   = parameters,
            t_start      = t_start,
            t_end        = t_end,
            drone_ids    = drone_ids or [],
        ))
        return self

    # ── Compilation ───────────────────────────────────────────────────────────

    def compile(self) -> ShowFile:
        """
        Compile the show definition into a ShowFile with polynomial trajectories.
        """
        # Build per-drone timeline of (time, position) waypoints
        # starting from home + takeoff, then each formation target.

        # drone_waypoints[i] = list of (t, Vec3 in global NED)
        drone_waypoints: list[list[tuple[float, Vec3]]] = [
            [] for _ in range(self._n)
        ]

        # t=0: all drones at home (ground)
        for i, spec in enumerate(self._drones):
            drone_waypoints[i].append((0.0, Vec3(spec.home_ned.n, spec.home_ned.e, 0.0)))

        # Takeoff phase: 15s to reach show altitude
        TAKEOFF_T = 15.0
        for i, spec in enumerate(self._drones):
            drone_waypoints[i].append(
                (TAKEOFF_T, Vec3(spec.home_ned.n, spec.home_ned.e, -SHOW_ALT_M))
            )

        # Accumulate current positions and clock
        t_now = TAKEOFF_T
        # After takeoff, drones hover at home XY positions
        current_ne = [(spec.home_ned.n, spec.home_ned.e) for spec in self._drones]

        # Assignment is stable across acts — compute once using initial positions
        # and preserve across acts (drone i always maps to its assigned slot)
        slot_assignment: Optional[list[int]] = None

        for act in self._acts:
            offsets = FORMATIONS[act.formation]
            cN, cE  = act.center_ne

            # Formation target positions
            targets = [(cN + dN, cE + dE) for (dN, dE) in offsets]

            if slot_assignment is None:
                # First act: assign optimally
                slot_assignment = assign(current_ne, targets)
            # Subsequent acts: re-assign from current positions each time
            else:
                slot_assignment = assign(current_ne, targets)

            # Each drone flies to its assigned target
            t_arrive = t_now + act.transition_s
            for i in range(self._n):
                j = slot_assignment[i]
                tN, tE = targets[j]
                drone_waypoints[i].append(
                    (t_arrive, Vec3(tN, tE, -SHOW_ALT_M))
                )

            # Hold: add a waypoint at hold end (same position)
            t_hold_end = t_arrive + act.hold_s
            for i in range(self._n):
                j = slot_assignment[i]
                tN, tE = targets[j]
                drone_waypoints[i].append(
                    (t_hold_end, Vec3(tN, tE, -SHOW_ALT_M))
                )
                current_ne[i] = (tN, tE)

            t_now = t_hold_end

        # Landing: 10s back to home XY, altitude = 0
        LAND_T = t_now + 10.0
        for i, spec in enumerate(self._drones):
            drone_waypoints[i].append(
                (LAND_T, Vec3(spec.home_ned.n, spec.home_ned.e, -SHOW_ALT_M))
            )
            drone_waypoints[i].append(
                (LAND_T + 5.0, Vec3(spec.home_ned.n, spec.home_ned.e, 0.0))
            )

        duration_s = LAND_T + 5.0

        # Fit polynomial trajectories
        trajectories: list[NominalTrajectory] = []
        for i in range(self._n):
            wps = drone_waypoints[i]
            times     = [w[0] for w in wps]
            positions = [w[1] for w in wps]
            traj = fit_trajectory(times, positions)
            traj.drone_id = i
            trajectories.append(traj)

        # Build LED tracks
        led_tracks = self._compile_led_tracks(duration_s)

        # Default envelopes (Phase 2 will fill actual radii)
        envelopes = [
            DroneEnvelope(
                drone_id = i,
                segments = [EnvelopeSegment(0.0, duration_s, 0.0)],
            )
            for i in range(self._n)
        ]

        metadata = ShowMetadata(
            name        = self._name,
            author      = self._author,
            venue_name  = self._venue,
            origin      = self._origin,
            duration_s  = duration_s,
            n_drones    = self._n,
        )

        return ShowFile(
            metadata          = metadata,
            drones            = self._drones,
            trajectories      = trajectories,
            led_tracks        = led_tracks,
            envelopes         = envelopes,
            reactive_bindings = self._reactive_bindings,
        )

    def _compile_led_tracks(self, duration_s: float) -> list[LedTrack]:
        tracks = []
        for i in range(self._n):
            # Filter cues for this drone
            drone_cues = [
                c for c in self._led_cues
                if not c.drone_ids or i in c.drone_ids
            ]
            if not drone_cues:
                # Default: white throughout
                keyframes = [
                    LedKeyframe(0.0, Color(1, 1, 1, 1)),
                    LedKeyframe(duration_s, Color(1, 1, 1, 1)),
                ]
            else:
                keyframes = [LedKeyframe(c.t, c.color) for c in drone_cues]
                keyframes.sort(key=lambda k: k.t)
            tracks.append(LedTrack(drone_id=i, keyframes=keyframes))
        return tracks
