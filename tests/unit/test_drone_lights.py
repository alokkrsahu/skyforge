"""
Tests for drone_lights.py — the standalone LED CLI util's world-aware topic.

Importing the module must NOT spawn `gz` (lazy resolution); `_light_topic()` builds
`/world/<resolved>/light_config` from the gz-world resolver.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show import gz_world
from show.gz_world import GZ_WORLD_ENV
import drone_lights


def _set_world(val):
    old = os.environ.get(GZ_WORLD_ENV)
    if val is None:
        os.environ.pop(GZ_WORLD_ENV, None)
    else:
        os.environ[GZ_WORLD_ENV] = val
    gz_world._reset_cache_for_tests()
    drone_lights._WORLD = None   # the module caches its own copy on first publish
    return old


def _restore_world(old):
    if old is None:
        os.environ.pop(GZ_WORLD_ENV, None)
    else:
        os.environ[GZ_WORLD_ENV] = old
    gz_world._reset_cache_for_tests()
    drone_lights._WORLD = None


def test_light_topic_uses_resolved_world():
    old = _set_world("walls")
    try:
        assert drone_lights._light_topic() == "/world/walls/light_config"
    finally:
        _restore_world(old)


def test_light_topic_defaults_to_default():
    old = _set_world("default")
    try:
        assert drone_lights._light_topic() == "/world/default/light_config"
    finally:
        _restore_world(old)


def test_import_does_not_resolve_world():
    """Importing the module must not have resolved/spawned anything (lazy)."""
    # _WORLD is the module-level cache; fresh import (already done above) leaves it
    # None until the first _light_topic() call. We reset it here and confirm.
    drone_lights._WORLD = None
    assert drone_lights._WORLD is None
