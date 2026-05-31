"""
Process supervisor — the gateway brings up the stack from the UI.

Spawns the launch scripts (t1 SITL, t6 commander+web, t5 player, t8 agents, t2 GUI, t7 QGC)
as tracked subprocesses with the SKYFORGE_* environment composed from the UI's options,
parses /tmp/px4_sitl_<i>.log for readiness, and tears everything down in the right order
(agents/commander/player → GUI → SITL) with the documented orphan cleanup so the next
bring-up isn't blocked by a stale mavsdk_server/px4-sock.

Pure helpers (compose_env, parse_sitl_ready, launch_argv) are unit-tested; the live spawn
needs PX4 and is verified manually (docs/TESTING.md). The commander+web bridge is spawned on
an INTERNAL port (SKYFORGE_WEB_PORT, default 8799) so it never collides with the gateway, and
the UI points its control/WS at that port (CORS-enabled on the bridge) — no reverse proxy.
"""
from __future__ import annotations

import asyncio
import glob
import os
import signal

# launch name → (script under runtime/, is_long_running)
LAUNCHERS = {
    "sitl":      ("t1_sitl.sh",       True),
    "gui":       ("t2_gazebo_gui.sh", True),
    "player":    ("t5_skyforge.sh",   True),
    "commander": ("t6_commander.sh",  True),
    "qgc":       ("t7_qgc.sh",        False),
    "agents":    ("t8_agents.sh",     True),
}
# Teardown order: kill flight runtimes first, then GUI, then the SITL stack.
_TEARDOWN_ORDER = ["agents", "player", "commander", "qgc", "gui", "sitl"]


def compose_env(opts: dict) -> dict:
    """UI bring-up options → SKYFORGE_* environment overrides (only set what's given)."""
    env: dict[str, str] = {}
    m = {"gcs": "SKYFORGE_GCS", "led": "SKYFORGE_LED_BACKEND", "fleet": "SKYFORGE_FLEET",
         "blackbox": "SKYFORGE_BLACKBOX", "failsafe_config": "SKYFORGE_FAILSAFE_CONFIG",
         "t0_epoch": "SKYFORGE_T0_EPOCH", "gz_world": "SKYFORGE_GZ_WORLD",
         "fail_mode": "SKYFORGE_FAIL_MODE"}
    for k, var in m.items():
        v = opts.get(k)
        if v is not None and v != "":          # forward a legitimate 0 (e.g. t0_epoch=0); skip None/""
            env[var] = str(v)
    if opts.get("autoabort"):
        env["SKYFORGE_AUTOABORT"] = "1"
    if opts.get("web"):
        env["SKYFORGE_WEB"] = "1"
        env["SKYFORGE_WEB_PORT"] = str(opts.get("web_port", 8799))
    return env


def parse_sitl_ready(log_text: str) -> int:
    """How many PX4 instances have signalled startup success in a t1 log aggregate."""
    return log_text.count("Startup script returned successfully")


def launch_argv(root: str, target: str, n: int = 4, arena: str = "default",
                show: str | None = None) -> list[str]:
    """Build the argv for a launcher target."""
    script, _ = LAUNCHERS[target]
    path = os.path.join(root, "runtime", script)
    if target == "sitl":
        return ["bash", path, str(n), arena]
    if target in ("player", "agents") and show:
        return ["bash", path, show] + ([str(n)] if target == "agents" else [])
    if target == "commander":
        return ["bash", path, str(n)]
    return ["bash", path]


class Supervisor:
    def __init__(self, root: str | None = None):
        self.root = root or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.procs: dict[str, asyncio.subprocess.Process] = {}

    async def _stop(self, p, grace: float = 5.0) -> None:
        if p is None or p.returncode is not None:
            return
        try:
            p.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(p.wait(), timeout=grace)
        except (asyncio.TimeoutError, Exception):
            try: p.kill()
            except Exception: pass

    async def spawn(self, target: str, *, n: int = 4, arena: str = "default",
                    show: str | None = None, opts: dict | None = None) -> int:
        await self._stop(self.procs.get(target))          # don't orphan a running instance
        argv = launch_argv(self.root, target, n=n, arena=arena, show=show)
        env  = {**os.environ, **compose_env(opts or {})}
        proc = await asyncio.create_subprocess_exec(*argv, env=env, cwd=self.root)
        self.procs[target] = proc
        return proc.pid

    def status(self) -> dict:
        return {name: {"pid": p.pid, "running": p.returncode is None}
                for name, p in self.procs.items()}

    async def teardown(self) -> list[str]:
        """SIGTERM tracked procs in safe order, then clean up orphan mavsdk_server/sock files
        (a stale server/sock otherwise denies arm on the next bring-up — known gotcha)."""
        killed = []
        for name in _TEARDOWN_ORDER:
            p = self.procs.get(name)
            if p is not None and p.returncode is None:
                await self._stop(p)                       # SIGTERM + AWAIT exit (kill if slow)
                killed.append(name)
        try:                                              # and actually wait for pkill to finish
            pk = await asyncio.create_subprocess_exec("pkill", "-9", "-f", "mavsdk_server")
            await pk.wait()
        except Exception:
            pass
        self.procs.clear()
        return killed


def register_supervisor(app) -> None:
    from pydantic import BaseModel
    sup = Supervisor()
    app.state.supervisor = sup

    class BringupReq(BaseModel):
        target: str; n: int = 4; arena: str = "default"; show: str | None = None; opts: dict = {}

    @app.post("/api/bringup")
    async def bringup(b: BringupReq):
        if b.target not in LAUNCHERS:
            return {"ok": False, "error": f"unknown target {b.target!r}"}
        pid = await sup.spawn(b.target, n=b.n, arena=b.arena, show=b.show, opts=b.opts)
        res = {"ok": True, "target": b.target, "pid": pid}
        if b.target == "commander":   # tell the UI where the live bridge is listening
            res["port"] = int(compose_env({**b.opts, "web": True}).get("SKYFORGE_WEB_PORT", 8799))
        return res

    @app.get("/api/procs")
    async def procs():
        return sup.status()

    @app.post("/api/teardown")
    async def teardown():
        return {"killed": await sup.teardown()}

    @app.get("/api/sitl_ready")
    async def sitl_ready():
        text = ""
        for f in glob.glob("/tmp/px4_sitl_*.log"):
            try:
                text += open(f).read()
            except OSError:
                pass
        return {"ready": parse_sitl_ready(text)}
