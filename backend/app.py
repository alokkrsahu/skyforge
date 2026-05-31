"""
SkyForge UI gateway — the always-up app: the offline plane (compile/validate/preflight/
export + formation catalog) and the static UI. No live runtime here (no MAVSDK), so it can
run before any bring-up.

    uvicorn backend.app:app --host 127.0.0.1 --port 8787

The live control + telemetry bridge is hosted separately by the commander process
(`SKYFORGE_WEB=1 ./t6_commander.sh N`, which serves the same control/WS API). Phase 3 adds
gateway-spawns-and-proxies-the-commander so a single origin carries both planes; for now run
the gateway for offline work, and the commander+web bridge for live flight.
"""
from __future__ import annotations

from fastapi import FastAPI

from .offline import register_offline
from .control import _mount_ui

app = FastAPI(title="SkyForge Operator UI — gateway")
register_offline(app)
_mount_ui(app)
