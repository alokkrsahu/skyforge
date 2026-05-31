"""
SkyForge UI gateway — the always-up app: the offline plane (compile/validate/preflight/
export + formation catalog), the process supervisor (spawns + streams the runtime stack), an
always-on lifecycle WebSocket, and the static UI. No live runtime here (no MAVSDK), so it can
run before any bring-up.

    uvicorn backend.app:app --host 127.0.0.1 --port 8787

Two-socket model: the gateway's /ws carries process/log/ready/lifecycle frames (always valid,
so the UI never floods a non-existent socket). The live control + telemetry bridge is hosted
separately by the spawned commander process (`SKYFORGE_WEB=1`), on its own internal port, with
its own /ws (telemetry/health/cmd_result); the UI connects that only once a commander is up.
"""
from __future__ import annotations

from fastapi import FastAPI

from .offline import register_offline
from .supervisor import register_supervisor
from .gateway_ws import register_gateway_ws
from .control import _mount_ui

app = FastAPI(title="SkyForge Operator UI — gateway")
register_offline(app)
register_supervisor(app)                 # sets app.state.supervisor (read by the /ws backlog)
register_gateway_ws(app)                 # always-on lifecycle /ws — kills the telemetry-/ws flood
_mount_ui(app)
