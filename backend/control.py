"""
SkyForge operator-UI backend — control plane + app factory.

The live bridge: REST control + a telemetry/health WebSocket, mounted on a FastAPI app
whose `state` carries the runtime objects (`commander`, `runtime`, `abort_event`,
`health_q`) INJECTED by the host (run_commander's in-loop `serve_web`, or a test). So this
module imports no runtime/MAVSDK code — it just calls the injected `FleetCommander`
coroutines on the same event loop (identical cooperative scheduling to the stdin REPL; no
lock). Every verb returns one human `str`; `classify()` turns it into a tri-state result.
"""
from __future__ import annotations

import asyncio
import secrets
import time

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Guard substrings (amber): the verb declined due to a precondition, not an error.
_GUARDS = ("not airborne", "in offboard", "Already airborne", "Unknown colour")


def broadcast(app: FastAPI, msg: dict) -> None:
    """Fan a frame out to every connected /ws subscriber (per-client latest-wins queues),
    so multiple windows all see cmd_result/health — a single shared queue would deliver
    each frame to only ONE client."""
    for q in list(getattr(app.state, "subscribers", ())):
        try:
            q.put_nowait(msg)
        except Exception:
            try: q.get_nowait(); q.put_nowait(msg)   # drop oldest
            except Exception: pass


def classify(msg: str, verb: str) -> dict:
    """Map a FleetCommander status string to {ok, guard, status, verb} (there is no
    structured error channel — errors start with 'Error', guards are known phrases)."""
    ok    = not msg.startswith("Error")
    guard = ok and any(s in msg for s in _GUARDS)
    return {"ok": ok, "guard": guard, "status": msg, "verb": verb}


# ── Request bodies (transition_s is first-class — fixes the REPL gap) ──────────
class TakeoffReq(BaseModel):   altitude_m: float = 5.0
class FormationReq(BaseModel): spec: str; transition_s: float = 6.0
class MoveReq(BaseModel):      dN: float = 0.0; dE: float = 0.0; transition_s: float = 5.0
class AltReq(BaseModel):       alt_m: float; transition_s: float = 5.0
class ColorReq(BaseModel):     name: str | None = None; r: float = 0.0; g: float = 0.8; b: float = 0.0
class LandReq(BaseModel):      stagger: bool = True
class RtlReq(BaseModel):       transition_s: float = 8.0


def register_control(app: FastAPI) -> None:
    cmd = lambda: app.state.commander    # injected FleetCommander

    def reply(msg: str, verb: str) -> dict:
        """Classify + fan the cmd_result out to EVERY connected client (command log /
        multi-window sync)."""
        res = classify(msg, verb)
        broadcast(app, {"type": "cmd_result", **res})
        return res

    @app.post("/api/cmd/takeoff")
    async def takeoff(b: TakeoffReq):   return reply(await cmd().takeoff(b.altitude_m), "takeoff")

    @app.post("/api/cmd/formation")
    async def formation(b: FormationReq): return reply(await cmd().formation(b.spec, b.transition_s), "formation")

    @app.post("/api/cmd/move")
    async def move(b: MoveReq):         return reply(await cmd().move(b.dN, b.dE, b.transition_s), "move")

    @app.post("/api/cmd/altitude")
    async def altitude(b: AltReq):      return reply(await cmd().set_altitude(b.alt_m, b.transition_s), "altitude")

    @app.post("/api/cmd/color")
    async def color(b: ColorReq):
        msg = await (cmd().set_color(b.name) if b.name is not None else cmd().set_color(b.r, b.g, b.b))
        return reply(msg, "color")

    @app.post("/api/cmd/hover")
    async def hover():                  return reply(await cmd().hover(), "hover")

    @app.post("/api/cmd/land")
    async def land(b: LandReq):         return reply(await cmd().land(b.stagger), "land")

    @app.post("/api/cmd/rtl")
    async def rtl(b: RtlReq):           return reply(await cmd().rtl(b.transition_s), "rtl")

    @app.post("/api/cmd/abort")
    async def abort():                  return reply(await cmd().abort(), "abort")  # E-STOP: no guard, no I/O

    @app.post("/api/session/kill")
    async def kill():
        # Immediate-drop E-STOP (matches the CLI's Ctrl-C → abort()) AND tear the session
        # down: abort() sets abort_flag+airborne=False (land now); abort_event makes
        # serve_web stop uvicorn so the gather completes and the process exits.
        await app.state.commander.abort()
        app.state.abort_event.set()
        return {"ok": True, "status": "ABORT — landing + tearing down session", "verb": "kill"}

    @app.get("/api/status")
    async def status():                 return {"text": await cmd().status()}

    @app.get("/api/snapshot")
    async def snapshot():               return await cmd().snapshot()

    # Single-writer command authority: one operator "takes command"; while a token is
    # held, mutating /api/cmd/* require it — EXCEPT abort (E-STOP is never lockable).
    @app.post("/api/command/acquire")
    async def acquire():
        app.state.command_token = secrets.token_hex(8)
        return {"token": app.state.command_token}

    @app.post("/api/command/release")
    async def release():
        app.state.command_token = None
        return {"ok": True}


def register_command_lock(app: FastAPI) -> None:
    @app.middleware("http")
    async def lock(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/cmd/") and not path.endswith("/abort"):
            tok = getattr(app.state, "command_token", None)
            if tok is not None and request.headers.get("x-command-token") != tok:
                verb = path.rsplit("/", 1)[-1]
                return JSONResponse(status_code=409, content={
                    "ok": False, "guard": True, "verb": verb,
                    "status": "another operator holds command (take command to fly)"})
        return await call_next(request)


def register_ws(app: FastAPI) -> None:
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q = asyncio.Queue(maxsize=32)                           # this client's frame mailbox
        app.state.subscribers.add(q)
        try:
            while True:
                snap = await app.state.commander.snapshot()
                await sock.send_json({"type": "telemetry", "t": time.monotonic(), **snap})
                while not q.empty():                            # cmd_result + health, fanned in
                    await sock.send_json(q.get_nowait())
                await asyncio.sleep(0.1)                         # 10 Hz telemetry cadence
        except WebSocketDisconnect:
            return
        finally:
            app.state.subscribers.discard(q)


def build_app(commander, runtime, abort_event, health_q=None) -> FastAPI:
    """Construct the live-bridge FastAPI app with the runtime objects injected on state."""
    app = FastAPI(title="SkyForge Operator UI — live bridge")
    app.state.commander   = commander
    app.state.runtime     = runtime
    app.state.abort_event = abort_event
    app.state.health_q    = health_q                    # 1 Hz FleetSummary (pumped by serve_web)
    app.state.subscribers = set()                       # per-client frame queues (fan-out)
    app.state.command_token = None                       # single-writer lock (None = open)
    # Order matters: Starlette runs the LAST-added middleware OUTERMOST. Register the
    # command lock FIRST, then CORS, so CORS wraps the lock — otherwise the lock's 409
    # short-circuit returns before CORS attaches headers and the browser sees a CORS error
    # instead of the guard JSON.
    register_command_lock(app)
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])
    register_control(app)
    register_ws(app)
    from .offline import register_offline               # only the CHEAP formation catalog
    register_offline(app, heavy=False)                  # live; compile/preflight stay on the gateway
    _mount_ui(app)
    return app


def _mount_ui(app: FastAPI) -> None:
    """Serve the built React app (ui/dist) at / when present (production); in dev the
    Vite server proxies /api and /ws here. No-op if the UI hasn't been built yet."""
    import pathlib
    from fastapi.staticfiles import StaticFiles
    dist = pathlib.Path(__file__).resolve().parents[1] / "ui" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
