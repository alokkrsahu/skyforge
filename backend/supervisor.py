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
import shlex
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
    mode: str = "background"            # "background" | "terminal" (open in a real Terminal window)


class LaunchReq(BaseModel):
    n: int = 4; arena: str = "default"; opts: dict = {}; mode: str = "background"


class Supervisor:
    def __init__(self, root: str | None = None, app=None):
        self.root = root or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self._app = app                                              # for broadcast (None → no-op)
        self.procs: dict[str, asyncio.subprocess.Process] = {}       # background-mode handles
        self.terms: dict[str, dict] = {}                             # terminal-mode tracking (no PID)
        self.rings: dict[str, collections.deque] = {}                # per-target log buffers
        self.readers: dict[str, asyncio.Task] = {}                   # per-target reader tasks
        self.state: dict[str, dict] = {}                             # per-target state machine
        self._ready_events: dict[str, asyncio.Event] = {}            # set when a target hits "ready"
        self._spawn_n: dict[str, int] = {}                           # expected N (SITL readiness)
        self._orchestrating = False                                  # one-click launch in flight?
        self._launch_task: asyncio.Task | None = None
        self._last_lifecycle: dict | None = None                     # replayed to new /ws clients
        self._last_bringup: dict | None = None

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
                self._ready_events.setdefault("sitl", asyncio.Event()).set()   # wake _await_ready
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

    async def _stop_one(self, target: str) -> None:
        """Stop a running instance of `target` (background OR terminal) and cancel its reader —
        don't orphan a respawn, and SIGTERM before cancelling so the child stops writing first."""
        if target in self.terms:                          # terminal mode: no PID handle → pkill the script
            script = LAUNCHERS.get(target, (None,))[0]
            if script:
                try:
                    pk = await asyncio.create_subprocess_exec("pkill", "-f", script)
                    await pk.wait()
                except Exception:
                    pass
            self.terms.pop(target, None)
        else:
            await self._stop(self.procs.get(target))      # SIGTERM first → child stops writing
        reader = self.readers.pop(target, None)            # then cancel the reader (no pipe deadlock)
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
                    show: str | None = None, opts: dict | None = None,
                    mode: str = "background") -> int:
        await self._stop_one(target)                      # don't orphan a running instance
        argv = launch_argv(self.root, target, n=n, arena=arena, show=show)
        over = compose_env(opts or {})
        self._spawn_n[target] = n
        self.rings[target]   = collections.deque(maxlen=_LOG_RING)
        self._ready_events[target] = asyncio.Event()
        if mode == "terminal":
            return await self._spawn_terminal(target, argv, over, n)
        proc = await asyncio.create_subprocess_exec(
            *argv, env={**os.environ, **over}, cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,             # one ordered, interleaved stream
            stdin=asyncio.subprocess.DEVNULL,             # headless t6 must never block on a TTY
        )
        self.procs[target]   = proc
        self._set_state(target, "starting", pid=proc.pid, code=None,
                        ready_n=0, ready_of=(n if target == "sitl" else 1))
        self.readers[target] = asyncio.create_task(self._read_loop(target, proc))
        return proc.pid

    async def _spawn_terminal(self, target: str, argv: list[str], over: dict, n: int) -> int:
        """Open a real macOS Terminal window running the launcher, tee'd to a log file we tail
        (we can't capture a Terminal window's stdout, so readiness/streaming come from the file).
        PID is unknown; stop is by pkill-by-script (see _stop_one)."""
        logf = f"/tmp/skyforge_{target}.log"
        try: open(logf, "w").close()                      # truncate so the tail starts clean
        except OSError: pass
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in over.items())
        inner = " ".join(shlex.quote(a) for a in argv)
        cmd = f"cd {shlex.quote(self.root)} && {env_prefix} {inner} 2>&1 | tee {shlex.quote(logf)}".strip()
        applescript = 'tell application "Terminal" to do script "%s"' % cmd.replace("\\", "\\\\").replace('"', '\\"')
        await asyncio.create_subprocess_exec("osascript", "-e", applescript)   # returns immediately
        self.terms[target] = {"logf": logf}
        self._set_state(target, "starting", pid=None, code=None,
                        ready_n=0, ready_of=(n if target == "sitl" else 1))
        self.readers[target] = asyncio.create_task(self._tail_loop(target, logf))
        return 0

    async def _tail_loop(self, target: str, logf: str) -> None:
        """Stream a terminal-mode process by tailing its tee'd log file (same frames/predicates
        as the pipe reader)."""
        pos = 0
        ring = self.rings[target]
        try:
            while True:
                chunk = ""
                try:
                    with open(logf) as f:
                        f.seek(pos); chunk = f.read(); pos = f.tell()
                except OSError:
                    pass
                for line in chunk.splitlines():
                    frame = {"type": "log", "target": target, "line": line, "t": time.monotonic()}
                    ring.append(frame); self._broadcast(frame)
                    self._evaluate_readiness(target, line)
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            raise
        finally:
            self._broadcast({"type": "proc", "procs": self.status()})

    def status(self) -> dict:
        out = {}
        for target, st in self.state.items():
            proc = self.procs.get(target)
            running = target in self.terms or (proc is not None and proc.returncode is None)
            out[target] = {"state": st.get("state", "idle"), "pid": st.get("pid"),
                           "running": running, "code": st.get("code"),
                           "ready_n": st.get("ready_n", 0), "ready_of": st.get("ready_of", 1)}
        return out

    def backlog(self) -> list[dict]:
        """Frames replayed to a newly-connected /ws client: current process status, recent logs,
        and the latest lifecycle/bringup so a window opened mid-launch catches up."""
        frames: list[dict] = [{"type": "proc", "procs": self.status()}]
        for ring in self.rings.values():
            frames.extend(ring)
        if self._last_lifecycle is not None:
            frames.append(self._last_lifecycle)
        if self._last_bringup is not None:
            frames.append(self._last_bringup)
        return frames

    # ── one-click orchestration: SITL → await ready → commander+web → await bridge ──
    def _lifecycle(self, phase: str, msg: str = "") -> None:
        frame = {"type": "lifecycle", "phase": phase, "msg": msg, "t": time.monotonic()}
        self._last_lifecycle = frame
        self._broadcast(frame)

    async def _await_ready(self, target: str, timeout: float = 240.0) -> bool:
        """Block until `target` signals ready (or fails/ times out)."""
        ev = self._ready_events.setdefault(target, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return self.state.get(target, {}).get("state") != "failed"

    async def _await_bridge(self, port: int, timeout: float = 90.0) -> bool:
        """Poll the spawned commander bridge until it answers — the authoritative up-signal
        (the '[web] bridge on http://' log line prints just before the socket binds)."""
        import httpx
        end = time.monotonic() + timeout
        async with httpx.AsyncClient() as client:
            while time.monotonic() < end:
                try:
                    r = await client.get(f"http://127.0.0.1:{port}/api/status", timeout=2.0)
                    if r.status_code == 200:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        return False

    async def orchestrate(self, n: int = 4, arena: str = "default",
                          opts: dict | None = None, mode: str = "background") -> None:
        """The one-click 'Launch stack': bring SITL up to N/N, then the commander+web bridge,
        emitting lifecycle frames the UI tracks; the final `bringup` frame carries the bridge
        port so the UI attaches its live socket. Runs as a background task; never raises to a
        request."""
        opts = opts or {}
        try:
            self._lifecycle("sitl_starting", f"spawning SITL ×{n} ({arena})")
            await self.spawn("sitl", n=n, arena=arena, opts=opts, mode=mode)
            if not await self._await_ready("sitl", timeout=240.0):
                failed = self.state.get("sitl", {}).get("state") == "failed"
                return self._lifecycle("failed" if failed else "timeout",
                                       "SITL did not reach N/N ready")
            self._lifecycle("sitl_ready", f"{n}/{n} SITL ready")

            web_port = int(compose_env({**opts, "web": True}).get("SKYFORGE_WEB_PORT", 8799))
            self._lifecycle("commander_starting", f"spawning commander+web on :{web_port}")
            await self.spawn("commander", n=n, opts={**opts, "web": True}, mode=mode)
            if not await self._await_bridge(web_port, timeout=90.0):
                return self._lifecycle("failed", "commander bridge never came up")

            proc = self.procs.get("commander")
            self._last_bringup = {"type": "bringup", "target": "commander", "port": web_port,
                                  "pid": proc.pid if proc is not None else None}
            self._broadcast(self._last_bringup)
            self._lifecycle("bridge_up", f"bridge on :{web_port}")
        finally:
            self._orchestrating = False

    async def teardown(self) -> list[str]:
        """Abort any in-flight launch, SIGTERM/pkill tracked procs in safe order, cancel their
        readers, then clean up orphan mavsdk_server + stale sockets/locks (a stale server/sock
        otherwise denies arm on the next bring-up — known clean-restart gotcha)."""
        if self._launch_task is not None and not self._launch_task.done():
            self._launch_task.cancel()
            try: await self._launch_task
            except BaseException: pass
            self._lifecycle("aborted", "launch aborted by teardown")
        self._orchestrating = False

        killed = []
        for name in _TEARDOWN_ORDER:
            p = self.procs.get(name)
            if (p is not None and p.returncode is None) or name in self.terms:
                await self._stop_one(name)                # SIGTERM/pkill + cancel reader
                killed.append(name)
        for target, reader in list(self.readers.items()):    # defensive: any reader not yet cancelled
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*self.readers.values(), return_exceptions=True)
        self.readers.clear()
        try:                                              # and actually wait for pkill to finish
            pk = await asyncio.create_subprocess_exec("pkill", "-9", "-f", "mavsdk_server")
            await pk.wait()
        except Exception:
            pass
        for pat in ("/tmp/px4-sock-*", "/tmp/px4_lock-*"):   # documented clean-restart cleanup
            for f in glob.glob(pat):
                try: os.remove(f)
                except OSError: pass
        for name in killed:
            self._set_state(name, "exited")
        self.procs.clear()
        self.terms.clear()
        self._broadcast({"type": "proc", "procs": self.status()})
        return killed


def register_supervisor(app) -> None:
    sup = Supervisor(app=app)
    app.state.supervisor = sup

    @app.post("/api/launch")
    async def launch(b: LaunchReq):
        """One-click: sequence SITL → commander+web in the background; progress streams over /ws
        (lifecycle frames + a `bringup` frame carrying the commander port)."""
        if sup._orchestrating:
            return {"ok": False, "error": "a launch is already in progress"}
        sup._orchestrating = True
        sup._launch_task = asyncio.create_task(sup.orchestrate(b.n, b.arena, b.opts, b.mode))
        return {"ok": True, "started": True}

    @app.post("/api/bringup")
    async def bringup(b: BringupReq):
        if b.target not in LAUNCHERS:
            return {"ok": False, "error": f"unknown target {b.target!r}"}
        pid = await sup.spawn(b.target, n=b.n, arena=b.arena, show=b.show, opts=b.opts, mode=b.mode)
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
