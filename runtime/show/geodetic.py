"""
Geodetic origin wiring — map the show's local NED frame to/from lat/lon/alt.

The compiler plans in local NED (metres from a venue origin); `VenueOrigin`
(lat/lon/alt/heading) has existed in the schema but was unused at runtime. This adds the
local-tangent-plane transform so a NED position can be turned into a geodetic fix (e.g. to
seed each drone's home, or to monitor against a geofence in lat/lon). The flat-earth /
small-area approximation is accurate to well under a metre over show-sized fields.

`heading` rotates the show's +N axis relative to true north (so you can point the show
any direction on the field).

DEFERRED (hardware): real **RTK** for the cm-level accuracy the 1.5 m margins assume, and
per-drone home reconciliation to a common datum. This module is the math seam for that.
"""
from __future__ import annotations

import math

R_EARTH = 6378137.0   # WGS-84 equatorial radius (m)


def ned_to_geodetic(n: float, e: float, d: float, origin) -> tuple[float, float, float]:
    """(N, E, D) metres from `origin` → (lat_deg, lon_deg, alt_m_MSL)."""
    h = getattr(origin, "heading", 0.0) or 0.0
    if h:                                                   # rotate show-frame → true north
        th = math.radians(h)
        n, e = (n * math.cos(th) - e * math.sin(th),
                n * math.sin(th) + e * math.cos(th))
    lat = origin.latitude  + math.degrees(n / R_EARTH)
    lon = origin.longitude + math.degrees(e / (R_EARTH * math.cos(math.radians(origin.latitude))))
    alt = origin.altitude - d                               # d is "down"; up = -d
    return (lat, lon, alt)


def geodetic_to_ned(lat: float, lon: float, alt: float, origin) -> tuple[float, float, float]:
    """(lat_deg, lon_deg, alt_m_MSL) → (N, E, D) metres in `origin`'s show frame (inverse)."""
    n = math.radians(lat - origin.latitude) * R_EARTH
    e = math.radians(lon - origin.longitude) * R_EARTH * math.cos(math.radians(origin.latitude))
    d = origin.altitude - alt
    h = getattr(origin, "heading", 0.0) or 0.0
    if h:                                                   # inverse rotation (true north → show)
        th = math.radians(h)
        n, e = (n * math.cos(th) + e * math.sin(th),
                -n * math.sin(th) + e * math.cos(th))
    return (n, e, d)


def has_origin(origin) -> bool:
    """True if a real geodetic origin is set (non-zero lat/lon) — vs the unset default."""
    return bool(origin) and (origin.latitude != 0.0 or origin.longitude != 0.0)


def describe_origin(origin) -> str:
    if not has_origin(origin):
        return "no geodetic origin (local NED only — indoor/known-origin OK; outdoor needs RTK)"
    return (f"origin {origin.latitude:.6f},{origin.longitude:.6f} @ {origin.altitude:.0f} m MSL "
            f"heading {getattr(origin, 'heading', 0.0):.0f}° — outdoor flight needs RTK for cm accuracy")
