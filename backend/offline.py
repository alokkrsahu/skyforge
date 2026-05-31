"""
Offline plane — author/compile/preflight/export + the formation catalog.

Wraps the top-level `cli.py` handlers (compile/validate/info/energy/preflight/export) by
calling them with a built argparse.Namespace and capturing stdout/stderr + exit code, plus
`compiler.formations` for the catalog + a preview. Pure/offline (no MAVSDK, no live
runtime), so it mounts on BOTH the always-up gateway and the live bridge app.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import os
import sys

from fastapi import FastAPI
from pydantic import BaseModel

# repo root on path so `import cli` / `compiler.formations` resolve
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _run(fn, **ns) -> dict:
    """Call a cli.cmd_* handler with a Namespace; capture stdout+stderr and exit code."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = fn(argparse.Namespace(**ns))
        except SystemExit as e:                       # handlers normally return ints
            code = e.code if isinstance(e.code, int) else 1
        except Exception as e:                        # never 500 the UI on a bad input
            buf.write(f"ERROR: {e}\n"); code = 1
    return {"exit": int(code or 0), "stdout": buf.getvalue()}


class ScriptReq(BaseModel):  script: str; output: str | None = None; min_sep: float = 1.5; tracking_margin: float = 0.0; no_validate: bool = False
class ShowReq(BaseModel):    show: str; min_sep: float = 1.5; tracking_margin: float = 0.0
class EnergyReq(BaseModel):  show: str; endurance: float = 600.0; reserve: float = 0.20
class PreflightReq(BaseModel): show: str; min_sep: float = 1.5; tracking_margin: float = 0.0; endurance: float = 600.0
class ExportReq(BaseModel):  show: str; drone: int | None = None; all: bool = True; output: str | None = None
class PreviewReq(BaseModel): spec: str; n: int = 16; min_spacing_m: float = 3.0; spacing_percentile: float = 20.0
class FlightlogReq(BaseModel): log: str


def register_offline(app: FastAPI, heavy: bool = True) -> None:
    """Mount the offline plane. The formation catalog/preview are CHEAP + pure and safe
    on the live flight loop, so build_app registers them with heavy=False. The CPU-bound
    handlers (compile/validate/preflight/…) are gateway-only (heavy=True) and run in a
    worker thread via asyncio.to_thread so a ~1 s compile never blocks the event loop —
    and they are NOT mounted on the live bridge (they redirect process-global stdout)."""
    import cli
    from compiler.formations import list_formations, get_formation

    @app.get("/api/formations")
    async def formations():
        return {"formations": list_formations()}

    @app.post("/api/formations/preview")
    async def preview(b: PreviewReq):
        try:
            pts = get_formation(b.spec, b.n, min_spacing_m=b.min_spacing_m,
                                spacing_percentile=b.spacing_percentile)
            return {"ok": True, "points": [list(p) for p in pts]}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    if not heavy:
        return

    @app.post("/api/compile")
    async def compile_show(b: ScriptReq):
        return await asyncio.to_thread(_run, cli.cmd_compile, script=b.script, output=b.output,
                    min_sep=b.min_sep, tracking_margin=b.tracking_margin, no_validate=b.no_validate)

    @app.post("/api/validate")
    async def validate_show(b: ShowReq):
        return await asyncio.to_thread(_run, cli.cmd_validate, show=b.show,
                    min_sep=b.min_sep, tracking_margin=b.tracking_margin)

    @app.post("/api/info")
    async def info_show(b: ShowReq):
        return await asyncio.to_thread(_run, cli.cmd_info, show=b.show)

    @app.post("/api/energy")
    async def energy_show(b: EnergyReq):
        return await asyncio.to_thread(_run, cli.cmd_energy, show=b.show,
                    endurance=b.endurance, reserve=b.reserve)

    @app.post("/api/preflight")
    async def preflight_show(b: PreflightReq):
        r = await asyncio.to_thread(_run, cli.cmd_preflight, show=b.show, min_sep=b.min_sep,
                 tracking_margin=b.tracking_margin, endurance=b.endurance)
        r["verdict"] = "GO" if r["exit"] == 0 else "NO-GO"     # the arm gate
        return r

    @app.post("/api/export")
    async def export_show(b: ExportReq):
        return await asyncio.to_thread(_run, cli.cmd_export, show=b.show,
                    drone=b.drone, all=b.all, output=b.output)

    @app.post("/api/flightlog")
    async def flightlog(b: FlightlogReq):
        return await asyncio.to_thread(_run, cli.cmd_flightlog, log=b.log)
