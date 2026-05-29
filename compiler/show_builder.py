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
from compiler.assignment import assign_nocross as assign, band_assignment
from compiler.formations import get_formation
from compiler.trajectory_generator import fit_trajectory

TAKEOFF_ALT_M = 5.0
SHOW_ALT_M    = 5.0

# Altitude-layered transitions: drones whose horizontal paths would collide are
# placed in different vertical bands during the move, then reconverge to show
# altitude at the hold. Bands are spaced >= the planned separation so different
# bands can never collide. A conflict-free transition uses band 0 only (no climb),
# so flat shows stay flat.
MIN_SEP_M       = 1.5
LAYER_SPACING_M = 1.6     # vertical gap between bands (> MIN_SEP_M)
CLIMB_FRAC      = 0.2     # fraction of the transition spent climbing / descending
LAYER_MARGIN_M  = 1.0     # band-assignment edges out to MIN_SEP_M + this, so the
                          # climb/descend ramp zones and bowed paths stay separated
TAKEOFF_T       = 15.0    # takeoff phase duration (ground → show altitude)
LAND_TRANS_S    = 10.0    # landing reconverge transition (formation → home pads)
LAND_DESCEND_S  = 5.0     # final vertical descent to ground


@dataclass
class _Act:
    formation:    str | list[tuple[float, float]]   # name or explicit (dN, dE) list
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
        layer_transitions: bool = True,
        min_sep_m:         float = MIN_SEP_M,
        layer_spacing_m:   float = LAYER_SPACING_M,
    ):
        self._name   = name
        self._drones = drones
        self._n      = len(drones)
        self._origin = origin or VenueOrigin()
        self._author = author
        self._venue  = venue
        # Altitude layering (see module docstring). On by default; only adds
        # vertical motion where horizontal paths actually conflict.
        self._layer_transitions = layer_transitions
        self._min_sep_m         = min_sep_m
        self._layer_spacing_m   = layer_spacing_m

        self._acts:              list[_Act]            = []
        self._led_cues:          list[_LedCue]         = []
        self._reactive_bindings: list[ReactiveBinding] = []

    # ── Authoring API ─────────────────────────────────────────────────────────

    def add_act(
        self,
        formation:    str | list[tuple[float, float]],
        center_ne:    tuple[float, float],
        transition_s: float,
        hold_s:       float,
    ) -> "ShowBuilder":
        """
        Add a choreography act.

        formation  built-in name ("circle", "grid", "star", "text:ALOK", …),
                   a text spec  ("text:HELLO:scale=3.0"),
                   or a list of (dN, dE) offset tuples for fully custom art.
        """
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

    def _bands_for(self, current_ne, targets, slot_assignment, transition_s) -> list[int]:
        """Vertical band per drone for a transition (0 = stay at show altitude)."""
        if self._layer_transitions and transition_s > 0:
            return band_assignment(
                current_ne, targets, slot_assignment, self._min_sep_m + LAYER_MARGIN_M
            )
        return [0] * self._n

    def _append_transition(
        self, wps, current_ne, targets, slot_assignment, bands, t_start, transition_s,
    ) -> list[tuple[float, float]]:
        """
        Append ONE transition to each drone's waypoint list: climb→cross→descend
        through its vertical band (band 0 = straight, no climb), arriving at show
        altitude at t_start+transition_s. Returns the new current_ne (assigned
        slot positions). Shared by formation acts AND the landing reconverge.
        """
        t_arrive = t_start + transition_s
        cf       = CLIMB_FRAC
        new_ne: list[tuple[float, float]] = []
        for i in range(self._n):
            tN, tE   = targets[slot_assignment[i]]
            cN0, cE0 = current_ne[i]
            if bands[i] > 0 and transition_s > 0:
                # Phase-separated move so banded drones can NEVER collide:
                #   climb  — straight UP at the start slot (slots are >= min_sep apart),
                #   cross  — horizontal at the band altitude (conflicting drones are in
                #            different bands, so >= layer_spacing apart vertically),
                #   descend — straight DOWN at the target slot.
                # No diagonal motion means no drone is ever both horizontally close to a
                # neighbour AND at the same altitude during a transition.
                band_d = -SHOW_ALT_M - bands[i] * self._layer_spacing_m
                wps[i].append((t_start + cf * transition_s,       Vec3(cN0, cE0, band_d)))   # climb
                wps[i].append((t_arrive - cf * transition_s,      Vec3(tN,  tE,  band_d)))   # cross
            wps[i].append((t_arrive, Vec3(tN, tE, -SHOW_ALT_M)))                              # descend / arrive
            new_ne.append((tN, tE))
        return new_ne

    def transition_windows(self) -> list[tuple[float, float]]:
        """
        (t_start, t_arrive) for every transition in compile order — the formation
        acts then the landing reconverge. Used by the verified-layering loop to
        sample each transition's actual trajectories. MUST match compile()'s timing.
        """
        t = TAKEOFF_T
        out: list[tuple[float, float]] = []
        for act in self._acts:
            t_arrive = t + act.transition_s
            out.append((t, t_arrive))
            t = t_arrive + act.hold_s
        out.append((t, t + LAND_TRANS_S))   # landing reconverge
        return out

    def compile(self, band_plan: list | None = None) -> ShowFile:
        """
        Compile the show definition into a ShowFile with polynomial trajectories.

        band_plan: optional per-transition vertical-band override (indexed in
        transition_windows() order: acts then landing). Entry None → use the
        default straight-line band_assignment for that transition; a list[int] →
        use those bands. The verified-layering loop drives this with bands derived
        from conflicts detected on the actual fitted splines. None everywhere
        reproduces the un-overridden compile exactly.
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

        # Takeoff phase: rise in place to show altitude
        for i, spec in enumerate(self._drones):
            drone_waypoints[i].append(
                (TAKEOFF_T, Vec3(spec.home_ned.n, spec.home_ned.e, -SHOW_ALT_M))
            )

        # Accumulate current positions and clock
        t_now = TAKEOFF_T
        # After takeoff, drones hover at home XY positions
        current_ne = [(spec.home_ned.n, spec.home_ned.e) for spec in self._drones]

        def _bands(curr, tgts, slots, trans_s, t_idx):
            # band_plan override (verified layering) takes precedence; else default.
            if band_plan is not None and band_plan[t_idx] is not None:
                return band_plan[t_idx]
            return self._bands_for(curr, tgts, slots, trans_s)

        for act_idx, act in enumerate(self._acts):
            # Size the formation to the fleet: scale up so neighbours clear the
            # planned separation (+ margin). Holds are stationary, so this is what
            # keeps a 100-drone circle/star/spiral from packing sub-metre.
            offsets = get_formation(
                act.formation, self._n, min_spacing_m=self._min_sep_m + 1.0
            )
            cN, cE  = act.center_ne
            targets = [(cN + dN, cE + dE) for (dN, dE) in offsets]

            # Assign slots, altitude-layer the transition, and emit climb/cross/
            # descend waypoints (a conflict-free transition stays flat).
            slot_assignment = assign(current_ne, targets, self._min_sep_m)
            bands = _bands(current_ne, targets, slot_assignment, act.transition_s, act_idx)
            t_arrive   = t_now + act.transition_s
            current_ne = self._append_transition(
                drone_waypoints, current_ne, targets, slot_assignment, bands,
                t_now, act.transition_s,
            )

            # Hold: stationary waypoint at hold end (same position)
            t_hold_end = t_arrive + act.hold_s
            for i in range(self._n):
                tN, tE = current_ne[i]
                drone_waypoints[i].append((t_hold_end, Vec3(tN, tE, -SHOW_ALT_M)))
            t_now = t_hold_end

        # Landing reconverge — route through the SAME assignment + layering as a
        # transition (this is ~89% of the residual conflicts). Pads are reassigned
        # to minimise crossings; drones then descend straight down to their pad.
        land_idx        = len(self._acts)
        home_targets    = [(s.home_ned.n, s.home_ned.e) for s in self._drones]
        land_assignment = assign(current_ne, home_targets, self._min_sep_m)
        land_bands      = _bands(current_ne, home_targets, land_assignment, LAND_TRANS_S, land_idx)
        LAND_T          = t_now + LAND_TRANS_S
        current_ne      = self._append_transition(
            drone_waypoints, current_ne, home_targets, land_assignment, land_bands,
            t_now, LAND_TRANS_S,
        )
        for i in range(self._n):
            pN, pE = current_ne[i]
            drone_waypoints[i].append((LAND_T + LAND_DESCEND_S, Vec3(pN, pE, 0.0)))

        duration_s = LAND_T + LAND_DESCEND_S

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
