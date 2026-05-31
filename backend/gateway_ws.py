"""
Always-on gateway WebSocket (`/ws` on :8787).

The gateway serves the UI before any commander exists, so the UI needs a socket that is
ALWAYS valid — otherwise it connects to a non-existent telemetry `/ws` and floods the console
with failed-handshake 400s (the original bug). This socket carries the lifecycle planes the
gateway owns: process status, streamed subprocess logs, SITL readiness, and bring-up progress.
It is purely event-driven (blocks on the fan-out queue) and touches NO MAVSDK/telemetry — live
telemetry stays on the spawned commander bridge's own `/ws`.

Frame contract (gateway → UI):
  {type:"proc",      procs:{target:{state,pid,running,code,ready_n,ready_of}}}
  {type:"log",       target, line, t}
  {type:"ready",     target, n, of}
  {type:"lifecycle", phase, msg, t}     phase ∈ sitl_starting|sitl_ready|commander_starting|
                                                bridge_up|failed|timeout|aborted
  {type:"bringup",   target:"commander", port, pid}
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .pubsub import ensure_subscribers


def register_gateway_ws(app: FastAPI) -> None:
    ensure_subscribers(app)

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q = asyncio.Queue(maxsize=256)                  # log lines are bursty → deeper than the bridge's
        app.state.subscribers.add(q)
        try:
            sup = getattr(app.state, "supervisor", None)
            if sup is not None:                          # replay backlog: current procs + recent logs
                for frame in sup.backlog():
                    await sock.send_json(frame)
            while True:                                  # event-driven: forward whatever is broadcast
                await sock.send_json(await q.get())
        except WebSocketDisconnect:
            return
        finally:
            app.state.subscribers.discard(q)
