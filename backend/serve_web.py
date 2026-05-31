"""
The Mode-A bridge: run FastAPI as a coroutine ON THE RUNTIME'S OWN asyncio loop.

`run_commander.main()` appends `("web", serve_web(commander, runtime, abort_event, health_q))`
to its existing gather (behind $SKYFORGE_WEB, replacing cli_loop). Because we drive
`uvicorn.Server.serve()` on the current loop — NEVER `uvicorn.run()`/`asyncio.run()`, which
would try to start a nested loop and crash — request handlers run with the same cooperative
scheduling as the stdin REPL and can `await commander.<verb>()` directly, no locks, no
cross-thread marshalling.
"""
from __future__ import annotations

import asyncio

from .control import build_app


async def _shutdown_on_abort(server, abort_event) -> None:
    """Stop uvicorn when the session is aborted/killed, so serve_web returns and the
    run_commander gather completes (otherwise the process would hang after a Kill)."""
    await abort_event.wait()
    server.should_exit = True


async def serve_web(commander, runtime, abort_event, health_q=None,
                    host: str = "127.0.0.1", port: int | None = None) -> None:
    import os
    import uvicorn
    if port is None:                                   # gateway spawns on an internal port
        port = int(os.environ.get("SKYFORGE_WEB_PORT", "8787"))
    app    = build_app(commander, runtime, abort_event, health_q)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            loop="none", lifespan="off")
    server = uvicorn.Server(config)
    asyncio.create_task(_shutdown_on_abort(server, abort_event))
    print(f"[web] SkyForge operator UI bridge on http://{host}:{port}")
    await server.serve()                      # runs on the caller's loop (run_commander's)
