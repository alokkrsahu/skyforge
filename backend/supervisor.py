"""
Process supervisor — the gateway brings up the runtime stack from the UI.

Spawns the launch scripts (t1 SITL, t6 commander+web, t5 player, t8 agents, t2 GUI, t7 QGC)
as tracked background subprocesses with the SKYFORGE_* environment composed from the UI's
options, CAPTURES their stdout/stderr and streams every line to the browser as a `log` frame,
tracks a per-process state machine (idle→starting→ready→running→exited/failed) driven by each
script's readiness markers, and tears everything down in the right order (agents/commander/
player → GUI → SITL) with the documented orphan cleanup so the next bring-up isn't blocked by
a stale mavsdk_server/px4-sock.

Pure helpers (compose_env, parse_sitl_ready, launch_argv) are unit-tested; live spawning needs
PX4 and is exercised hermetically with a fake subprocess (backend/tests/test_supervisor.py).
The commander+web bridge is spawned on an INTERNAL port (SKYFORGE_WEB_PORT, default 8799) so it
never collides with the gateway; the UI points its control/WS at that port (CORS-enabled on the
bridge) — no reverse proxy. All process/log/lifecycle frames fan out over the gateway's /ws via
the shared pubsub hub (backend/pubsub.py).
"""
from __future__ import annotations

import asyncio
import collections
import glob
import os
import signal
import time

from pydantic import BaseModel

from .pubsub import broadcast

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

_LOG_RING = 500            # per-process log lines retained for backlog replay


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


def _default_state() -> dict:
    return {"state": "idle", "pid": None, "code": None, "ready_n": 0, "ready_of": 1}


# Request body MUST be module-level: `from __future__ import annotations` stringifies the
# endpoint annotation, and FastAPI resolves it via the module globals — a class defined inside
# register_supervisor would be invisible there and FastAPI would treat `b` as a query param
# (a latent 422 that broke /api/bringup over HTTP).
class BringupReq(BaseModel):
    target: str; n: int = 4; arena: str = "default"; show: str | None = None; opts: dict = {}


class Supervisor:
    def __init__(self, root: str | None = None, app=None):
        self.root = root or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self._app = app                                              # for broadcast (None → no-op)
        self.procs: dict[str, asyncio.subprocess.Process] = {}       # background-mode handles
        self.rings: dict[str, collections.deque] = {}                # per-target log buffers
        self.readers: dict[str, asyncio.Task] = {}                   # per-target reader tasks
        self.state: dict[str, dict] = {}                             # per-target state machine
        self._ready_events: dict[str, asyncio.Event] = {}            # set when a target hits "ready"
        self._spawn_n: dict[str, int] = {}                           # expected N (SITL readiness)

    # ── broadcast helpers ──────────────────────────────────────────────────────
    def _broadcast(self, frame: dict) -> None:
        if self._app is not None:
            broadcast(self._app, frame)

    def _set_state(self, target: str, state: str | None = None, **fields) -> None:
        cur = self.state.setdefault(target, _default_state())
        if state is not None:
            cur["state"] = state
        cur.update(fields)
        self._broadcast({"type": "proc", "procs": self.status()})

    # ── readiness predicates (per target, from each script's stdout markers) ─────
    def _evaluate_readiness(self, target: str, line: str) -> None:
        if target == "sitl":
            if "Startup script returned successfully" in line:
                st = self.state.setdefault(target, _default_state())
                n  = st.get("ready_n", 0) + 1
                of = self._spawn_n.get("sitl", st.get("ready_of", 1))
                self._set_state("sitl", ready_n=n, ready_of=of)
                self._broadcast({"type": "ready", "target": "sitl", "n": n, "of": of})
                if n >= of:
                    self._set_state("sitl", "running")
                    self._ready_events.setdefault("sitl", asyncio.Event()).set()
            elif "returned with return value:" in line or "FAILED after" in line:
                self._set_state("sitl", "failed")
        elif target == "commander":
            if "bridge on http://" in line:
                self._set_state("commander", "ready")
                self._ready_events.setdefault("commander", asyncio.Event()).set()
        elif target == "player":
            if "Launching show" in line:
                self._set_state("player", "running")
                self._ready_events.setdefault("player", asyncio.Event()).set()
        elif target == "agents":
            if "Launching" in line and "agent" in line:
                self._set_state("agents", "running")
                self._ready_events.setdefault("agents", asyncio.Event()).set()

    # ── process lifecycle ────────────────────────────────────────────────────────
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

    async def _stop_target(self, target: str) -> None:
        """Stop a running instance of `target` and cancel its reader (don't orphan a respawn)."""
        await self._stop(self.procs.get(target))         # SIGTERM first → child stops writing
        reader = self.readers.pop(target, None)           # then cancel the reader (no pipe deadlock)
        if reader is not None and not reader.done():
            reader.cancel()
            try: await reader
            except BaseException: pass

    async def _read_loop(self, target: str, proc) -> None:
        """Stream one process's merged stdout/stderr: ring-buffer + broadcast each line, run the
        readiness predicate, and mark `exited` when the pipe closes."""
        ring = self.rings[target]
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:                               # EOF — the process closed its pipe
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n")
                frame = {"type": "log", "target": target, "line": line, "t": time.monotonic()}
                ring.append(frame)
                self._broadcast(frame)
                self._evaluate_readiness(target, line)
            code = await proc.wait()
            self._set_state(target, "exited", code=code)
        except asyncio.CancelledError:
            raise
        finally:
            self._broadcast({"type": "proc", "procs": self.status()})

    async def spawn(self, target: str, *, n: int = 4, arena: str = "default",
                    show: str | None = None, opts: dict | None = None) -> int:
        await self._stop_target(target)                   # don't orphan a running instance
        argv = launch_argv(self.root, target, n=n, arena=arena, show=show)
        env  = {**os.environ, **compose_env(opts or {})}
        self._spawn_n[target] = n
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env, cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,             # one ordered, interleaved stream
            stdin=asyncio.subprocess.DEVNULL,             # headless t6 must never block on a TTY
        )
        self.procs[target]   = proc
        self.rings[target]   = collections.deque(maxlen=_LOG_RING)
        self._ready_events[target] = asyncio.Event()
        self._set_state(target, "starting", pid=proc.pid, code=None,
                        ready_n=0, ready_of=(n if target == "sitl" else 1))
        self.readers[target] = asyncio.create_task(self._read_loop(target, proc))
        return proc.pid

    def status(self) -> dict:
        out = {}
        for target, st in self.state.items():
            proc = self.procs.get(target)
            out[target] = {"state": st.get("state", "idle"), "pid": st.get("pid"),
                           "running": proc is not None and proc.returncode is None,
                           "code": st.get("code"),
                           "ready_n": st.get("ready_n", 0), "ready_of": st.get("ready_of", 1)}
        return out

    def backlog(self) -> list[dict]:
        """Frames replayed to a newly-connected /ws client: current process status + recent logs."""
        frames: list[dict] = [{"type": "proc", "procs": self.status()}]
        for ring in self.rings.values():
            frames.extend(ring)
        return frames

    async def teardown(self) -> list[str]:
        """SIGTERM tracked procs in safe order, cancel their readers, then clean up orphan
        mavsdk_server/sock files (a stale server/sock otherwise denies arm on the next
        bring-up — known gotcha)."""
        killed = []
        for name in _TEARDOWN_ORDER:
            p = self.procs.get(name)
            if p is not None and p.returncode is None:
                await self._stop(p)                       # SIGTERM + AWAIT exit (kill if slow)
                killed.append(name)
        for target, reader in list(self.readers.items()):    # cancel readers AFTER stopping procs
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*self.readers.values(), return_exceptions=True)
        self.readers.clear()
        try:                                              # and actually wait for pkill to finish
            pk = await asyncio.create_subprocess_exec("pkill", "-9", "-f", "mavsdk_server")
            await pk.wait()
        except Exception:
            pass
        for name in killed:
            self._set_state(name, "exited")
        self.procs.clear()
        self._broadcast({"type": "proc", "procs": self.status()})
        return killed


def register_supervisor(app) -> None:
    sup = Supervisor(app=app)
    app.state.supervisor = sup

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
