"""
Resolve the running Gazebo world (arena) name for the runtime.

The gz transport namespace is ``/world/<inner-world-name>/...`` where the inner
name comes from the SDF's ``<world name="...">`` — which is NOT always the filename
(``frictionless.sdf`` and Skyforge's ``default.sdf`` / ``forest_stage.sdf`` are all
inner-named ``default``). So the runtime must DISCOVER the name from the live server
rather than assume it from whatever arena was passed to ``t1_sitl.sh``.

``resolve_gz_world()`` is the single source of truth used to build every
``/world/<name>/...`` gz service path (LED backends, drone_lights). It is cached,
never raises, and never hangs:

  1. ``$SKYFORGE_GZ_WORLD`` (stripped, non-empty) — explicit override; also what unit
     tests set so they never spawn ``gz``.
  2. else parse ``gz topic -l`` for the first ``/world/<name>/clock`` topic (the same
     pattern PX4's px4-rc.gzsim uses to auto-detect the world).
  3. else ``"default"`` — the historical hardcoded value, so with no server reachable
     (or no env set) behavior is byte-for-byte what it was before this module existed.
"""
import os
import re
import subprocess

GZ_WORLD_ENV = "SKYFORGE_GZ_WORLD"
_DEFAULT_WORLD = "default"
_CLOCK_RE = re.compile(r"^/world/([^/]+)/clock$")   # one segment; rejects /world//clock and nested

_CACHED = None   # resolved world name, computed once


def resolve_gz_world() -> str:
    """Inner gz world name for the running sim. Cached; never raises/hangs."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    env = (os.environ.get(GZ_WORLD_ENV) or "").strip()
    if env:
        _CACHED = env
        return _CACHED

    _CACHED = _detect_from_server()
    return _CACHED


def _detect_from_server() -> str:
    """First ``/world/<name>/clock`` topic on the running server, or 'default'."""
    try:
        out = subprocess.run(
            ["gz", "topic", "-l"],
            capture_output=True, text=True, timeout=2.0,
            env={**os.environ, "GZ_IP": "127.0.0.1"},
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return _DEFAULT_WORLD
    for line in out.splitlines():
        m = _CLOCK_RE.match(line.strip())
        if m:
            return m.group(1)
    return _DEFAULT_WORLD


def _reset_cache_for_tests() -> None:
    """Clear the cache so a test can re-resolve under a different env."""
    global _CACHED
    _CACHED = None
