"""Deserialize a ShowFile from JSON or msgpack."""
from __future__ import annotations

import json
import msgpack

from .schema import (
    Color, DroneEnvelope, DroneSpec, EnvelopeSegment, LedKeyframe, LedTrack,
    NominalTrajectory, PolySegment, ReactiveBinding, ShowFile, ShowMetadata,
    VenueOrigin, Vec3,
)


# ── Low-level builders ────────────────────────────────────────────────────────

def _vec3(d: dict) -> Vec3:
    return Vec3(d["n"], d["e"], d["d"])

def _color(d: dict) -> Color:
    return Color(d["r"], d["g"], d["b"], d["a"])

def _origin(d: dict) -> VenueOrigin:
    return VenueOrigin(d["latitude"], d["longitude"], d["altitude"], d["heading"])

def _metadata(d: dict) -> ShowMetadata:
    return ShowMetadata(
        schema_version    = d["schema_version"],
        name              = d["name"],
        author            = d.get("author", ""),
        created_at        = d["created_at"],
        venue_name        = d.get("venue_name", ""),
        origin            = _origin(d["origin"]),
        duration_s        = d["duration_s"],
        n_drones          = d["n_drones"],
        validation_status = d.get("validation_status", "unvalidated"),
        # Compile-time safety contract (schema v2) — default to "unknown" so
        # pre-v2 show files still load.
        compile_min_sep_m     = d.get("compile_min_sep_m", 0.0),
        compile_deconflict_hz = d.get("compile_deconflict_hz", 0.0),
        compile_validate_hz   = d.get("compile_validate_hz", 0.0),
        deconflicted          = d.get("deconflicted", False),
        deconflict_resolved   = d.get("deconflict_resolved", True),
        envelopes_computed    = d.get("envelopes_computed", False),
    )

def _drone_spec(d: dict) -> DroneSpec:
    return DroneSpec(d["logical_id"], _vec3(d["home_ned"]), d.get("vehicle_type", "x500"))

def _poly_segment(d: dict) -> PolySegment:
    return PolySegment(
        d["t_start"], d["t_end"],
        d["coeffs_n"], d["coeffs_e"], d["coeffs_d"],
    )

def _trajectory(d: dict) -> NominalTrajectory:
    return NominalTrajectory(d["drone_id"], [_poly_segment(s) for s in d["segments"]])

def _led_keyframe(d: dict) -> LedKeyframe:
    return LedKeyframe(d["t"], _color(d["color"]))

def _led_track(d: dict) -> LedTrack:
    return LedTrack(d["drone_id"], [_led_keyframe(k) for k in d["keyframes"]])

def _envelope_segment(d: dict) -> EnvelopeSegment:
    return EnvelopeSegment(d["t_start"], d["t_end"], d["radius_m"])

def _drone_envelope(d: dict) -> DroneEnvelope:
    return DroneEnvelope(d["drone_id"], [_envelope_segment(s) for s in d["segments"]])

def _reactive_binding(d: dict) -> ReactiveBinding:
    return ReactiveBinding(
        input_source = d["input_source"],
        primitive    = d["primitive"],
        parameters   = d["parameters"],
        t_start      = d["t_start"],
        t_end        = d["t_end"],
        drone_ids    = d.get("drone_ids", []),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def _from_dict(d: dict) -> ShowFile:
    return ShowFile(
        metadata          = _metadata(d["metadata"]),
        drones            = [_drone_spec(x)         for x in d["drones"]],
        trajectories      = [_trajectory(x)         for x in d["trajectories"]],
        led_tracks        = [_led_track(x)          for x in d["led_tracks"]],
        envelopes         = [_drone_envelope(x)     for x in d["envelopes"]],
        reactive_bindings = [_reactive_binding(x)   for x in d["reactive_bindings"]],
        audio_file        = d.get("audio_file"),
    )


def from_json(path: str) -> ShowFile:
    with open(path) as f:
        return _from_dict(json.load(f))


def from_msgpack(path: str) -> ShowFile:
    with open(path, "rb") as f:
        return _from_dict(msgpack.unpackb(f.read(), raw=False))
