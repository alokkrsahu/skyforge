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

from .control import build_app


async def serve_web(commander, runtime, abort_event, health_q=None,
                    host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn
    app    = build_app(commander, runtime, abort_event, health_q)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            loop="none", lifespan="off")
    server = uvicorn.Server(config)
    print(f"[web] SkyForge operator UI bridge on http://{host}:{port}")
    await server.serve()                      # runs on the caller's loop (run_commander's)
