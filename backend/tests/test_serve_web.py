"""
serve_web shutdown wiring: aborting/killing the session must make uvicorn stop so the
run_commander gather can complete (otherwise the process hangs after Kill). We test the
watcher logic against a fake server (no real uvicorn / port bind).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.serve_web import _shutdown_on_abort


class _FakeServer:
    def __init__(self): self.should_exit = False


def test_shutdown_on_abort_sets_should_exit():
    async def body():
        srv = _FakeServer()
        ev = asyncio.Event()
        task = asyncio.create_task(_shutdown_on_abort(srv, ev))
        await asyncio.sleep(0)               # let the watcher park on ev.wait()
        assert srv.should_exit is False
        ev.set()
        await task
        assert srv.should_exit is True       # uvicorn told to stop → serve_web returns
    asyncio.run(body())
