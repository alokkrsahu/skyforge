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
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

# Guard substrings (amber): the verb declined due to a precondition, not an error.
_GUARDS = ("not airborne", "in offboard", "Already airborne", "Unknown colour")


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

    @app.post("/api/cmd/takeoff")
    async def takeoff(b: TakeoffReq):   return classify(await cmd().takeoff(b.altitude_m), "takeoff")

    @app.post("/api/cmd/formation")
    async def formation(b: FormationReq): return classify(await cmd().formation(b.spec, b.transition_s), "formation")

    @app.post("/api/cmd/move")
    async def move(b: MoveReq):         return classify(await cmd().move(b.dN, b.dE, b.transition_s), "move")

    @app.post("/api/cmd/altitude")
    async def altitude(b: AltReq):      return classify(await cmd().set_altitude(b.alt_m, b.transition_s), "altitude")

    @app.post("/api/cmd/color")
    async def color(b: ColorReq):
        msg = await (cmd().set_color(b.name) if b.name is not None else cmd().set_color(b.r, b.g, b.b))
        return classify(msg, "color")

    @app.post("/api/cmd/hover")
    async def hover():                  return classify(await cmd().hover(), "hover")

    @app.post("/api/cmd/land")
    async def land(b: LandReq):         return classify(await cmd().land(b.stagger), "land")

    @app.post("/api/cmd/rtl")
    async def rtl(b: RtlReq):           return classify(await cmd().rtl(b.transition_s), "rtl")

    @app.post("/api/cmd/abort")
    async def abort():                  return classify(await cmd().abort(), "abort")  # E-STOP: no guard, no I/O

    @app.post("/api/session/kill")
    async def kill():
        app.state.abort_event.set()     # hard session teardown (also player/agents E-STOP)
        return {"ok": True, "status": "session abort_event set", "verb": "kill"}

    @app.get("/api/status")
    async def status():                 return {"text": await cmd().status()}

    @app.get("/api/snapshot")
    async def snapshot():               return await cmd().snapshot()


def register_ws(app: FastAPI) -> None:
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        hq = app.state.health_q
        try:
            while True:
                snap = await app.state.commander.snapshot()
                await sock.send_json({"type": "telemetry", "t": time.monotonic(), **snap})
                if hq is not None:
                    while not hq.empty():
                        await sock.send_json(hq.get_nowait())   # 1 Hz FleetSummary frames
                await asyncio.sleep(0.1)                         # 10 Hz telemetry cadence
        except WebSocketDisconnect:
            return


def build_app(commander, runtime, abort_event, health_q=None) -> FastAPI:
    """Construct the live-bridge FastAPI app with the runtime objects injected on state."""
    app = FastAPI(title="SkyForge Operator UI — live bridge")
    app.state.commander   = commander
    app.state.runtime     = runtime
    app.state.abort_event = abort_event
    app.state.health_q    = health_q
    register_control(app)
    register_ws(app)
    return app
