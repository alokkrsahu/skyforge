"""
Tests for the geodetic origin transform (runtime/show/geodetic.py). Pure math —
local-tangent-plane NED <-> lat/lon/alt, with heading rotation and round-trip.
"""
import math
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show.geodetic import ned_to_geodetic, geodetic_to_ned, has_origin, describe_origin


def _origin(lat=51.75, lon=-1.26, alt=100.0, heading=0.0):
    return types.SimpleNamespace(latitude=lat, longitude=lon, altitude=alt, heading=heading)


def test_origin_maps_to_itself():
    o = _origin()
    lat, lon, alt = ned_to_geodetic(0.0, 0.0, 0.0, o)
    assert abs(lat - o.latitude) < 1e-12 and abs(lon - o.longitude) < 1e-12 and alt == o.altitude


def test_directions():
    o = _origin()
    assert ned_to_geodetic(100.0, 0.0, 0.0, o)[0] > o.latitude    # +N → +lat
    assert ned_to_geodetic(0.0, 100.0, 0.0, o)[1] > o.longitude   # +E → +lon
    assert ned_to_geodetic(0.0, 0.0, 10.0, o)[2] < o.altitude     # +D (down) → lower alt


def test_heading_rotation():
    # heading 90°: the show's +N axis points East, so a +N move changes LONGITUDE.
    o = _origin(heading=90.0)
    lat, lon, _ = ned_to_geodetic(100.0, 0.0, 0.0, o)
    assert abs(lat - o.latitude) < 1e-6      # latitude barely changes
    assert lon > o.longitude                  # longitude moves instead


def test_round_trip():
    o = _origin(heading=30.0)
    for ned in [(0.0, 0.0, 0.0), (50.0, -20.0, -8.0), (-100.0, 75.0, 12.0)]:
        g = ned_to_geodetic(*ned, o)
        back = geodetic_to_ned(*g, o)
        assert all(abs(a - b) < 1e-3 for a, b in zip(ned, back))


def test_has_origin_and_describe():
    assert not has_origin(_origin(lat=0.0, lon=0.0))
    assert has_origin(_origin())
    assert "RTK" in describe_origin(_origin())
    assert "local NED" in describe_origin(_origin(lat=0.0, lon=0.0))
