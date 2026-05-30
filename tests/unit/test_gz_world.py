"""
Tests for the gz-world resolver (show.gz_world).

Must be hermetic: env-first resolution + a monkeypatched subprocess so no real
`gz` is ever spawned, and the resolver never raises/hangs.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../runtime"))

from show import gz_world
from show.gz_world import resolve_gz_world, GZ_WORLD_ENV


def _reset(env_val=None):
    if env_val is None:
        os.environ.pop(GZ_WORLD_ENV, None)
    else:
        os.environ[GZ_WORLD_ENV] = env_val
    gz_world._reset_cache_for_tests()


def test_env_override_wins():
    _reset("foo")
    try:
        assert resolve_gz_world() == "foo"
    finally:
        _reset()


def test_blank_env_is_ignored():
    _reset("   ")   # whitespace-only → treated as unset
    orig = gz_world.subprocess.run
    gz_world.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    try:
        assert resolve_gz_world() == "default"
    finally:
        gz_world.subprocess.run = orig
        _reset()


def test_fallback_when_gz_missing():
    _reset()
    orig = gz_world.subprocess.run

    def boom(*a, **k):
        raise FileNotFoundError("gz not installed")

    gz_world.subprocess.run = boom
    try:
        assert resolve_gz_world() == "default"
    finally:
        gz_world.subprocess.run = orig
        _reset()


def test_parses_first_clock_topic():
    _reset()
    orig = gz_world.subprocess.run

    def fake(*a, **k):
        return types.SimpleNamespace(
            stdout="/clock\n/world/walls/clock\n/world/walls/pose/info\n")

    gz_world.subprocess.run = fake
    try:
        assert resolve_gz_world() == "walls"
    finally:
        gz_world.subprocess.run = orig
        _reset()


def _detect_with_stdout(stdout: str) -> str:
    _reset()
    orig = gz_world.subprocess.run
    gz_world.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=stdout)
    try:
        return resolve_gz_world()
    finally:
        gz_world.subprocess.run = orig
        _reset()


def test_regex_rejects_empty_world():
    assert _detect_with_stdout("/world//clock\n") == "default"


def test_regex_rejects_nested_world():
    assert _detect_with_stdout("/world/a/b/clock\n") == "default"


def test_regex_accepts_underscored_name():
    assert _detect_with_stdout("/world/forest_stage/clock\n") == "forest_stage"


def test_caches_result():
    _reset("first")
    try:
        assert resolve_gz_world() == "first"
        os.environ[GZ_WORLD_ENV] = "second"   # cache must NOT pick this up
        assert resolve_gz_world() == "first"
    finally:
        _reset()
