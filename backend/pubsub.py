"""
Tiny WebSocket fan-out hub, shared by the live bridge (control.py) and the always-up
gateway (gateway_ws.py).

Every connected client gets its OWN bounded `asyncio.Queue` registered in
`app.state.subscribers`; `broadcast()` pushes a frame to each (dropping that client's
oldest frame on overflow). A single shared queue would deliver each frame to only one
client — multiple browser windows must each see telemetry/log/cmd_result.
"""
from __future__ import annotations


def ensure_subscribers(app) -> None:
    """Idempotently make `app.state.subscribers` a set (build_app already sets one)."""
    if not hasattr(app.state, "subscribers"):
        app.state.subscribers = set()


def broadcast(app, msg: dict) -> None:
    """Fan a frame out to every connected /ws subscriber (per-client latest-wins queues)."""
    for q in list(getattr(app.state, "subscribers", ())):
        try:
            q.put_nowait(msg)
        except Exception:
            try: q.get_nowait(); q.put_nowait(msg)   # drop oldest
            except Exception: pass
