"""FastAPI app factory for the local web visualizer."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from codeboarding_web.diagram import load_cytoscape
from codeboarding_web.events import EventBus, format_sse
from codeboarding_web.runner import AnalysisRunner
from codeboarding_web.state import RunBusyError, RunState
from codeboarding_web.watcher import RepoWatcher

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    """Body for POST /api/run."""

    scope: str = "full"
    base_ref: str = "HEAD~1"
    target_ref: str = "HEAD"


class WatchRequest(BaseModel):
    """Body for POST /api/watch."""

    enabled: bool


def create_app(
    repo_path: Path, output_dir: Path, project_name: str, depth_level: int = 1, watch: bool = False
) -> FastAPI:
    """Build and return the FastAPI application."""
    state = RunState()
    bus = EventBus()
    runner = AnalysisRunner(repo_path, output_dir, project_name, state, bus, depth_level)

    def on_change() -> None:
        """Trigger an incremental re-analysis when a source file changes."""
        if not app.state.watch_enabled:
            return
        if state.is_busy:
            return
        if not (output_dir / "analysis.json").exists():
            bus.publish_threadsafe("watch_triggered", {"status": "no_baseline"})
            return
        bus.publish_threadsafe("watch_triggered", {"status": "running"})
        try:
            runner.start("incremental", base_ref="HEAD", target_ref="")
        except RunBusyError:
            return

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        bus.set_loop(asyncio.get_running_loop())
        stop = asyncio.Event()
        task = asyncio.create_task(RepoWatcher(repo_path, output_dir, on_change).run(stop))
        yield
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="CodeBoarding", lifespan=lifespan)
    app.state.runner = runner
    app.state.run_state = state
    app.state.bus = bus
    app.state.watch_enabled = watch

    @app.get("/api/status")
    def status() -> dict:
        """Return current run state and project metadata."""
        return {
            "phase": state.phase.value,
            "run_id": state.run_id,
            "scope": state.scope,
            "error": state.error,
            "project": project_name,
            "has_baseline": (output_dir / "analysis.json").exists(),
            "watch_enabled": app.state.watch_enabled,
        }

    @app.get("/api/diagram.json")
    def diagram() -> JSONResponse:
        """Return Cytoscape elements for the current analysis, or 404."""
        elements = load_cytoscape(output_dir, project_name)
        if elements is None:
            raise HTTPException(status_code=404, detail="no analysis yet")
        return JSONResponse(elements)

    @app.post("/api/run")
    def run(req: RunRequest) -> dict:
        """Start a new analysis run; 400 on bad scope, 409 if busy."""
        if req.scope not in {"full", "incremental"}:
            raise HTTPException(status_code=400, detail="scope must be full or incremental")
        try:
            run_id = runner.start(req.scope, req.base_ref, req.target_ref)
        except RunBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id, "scope": req.scope}

    @app.post("/api/watch")
    def watch_toggle(req: WatchRequest) -> dict:
        """Enable or disable the file watcher."""
        app.state.watch_enabled = req.enabled
        return {"watch_enabled": app.state.watch_enabled}

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        """Open an SSE stream of analysis events for one subscriber."""
        q = bus.subscribe()

        async def stream() -> AsyncGenerator[str, None]:
            try:
                while True:
                    message = await q.get()
                    yield format_sse(message["event"], message["data"])
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

        @app.get("/")
        def index() -> FileResponse:
            """Serve the single-page application shell."""
            return FileResponse(str(_STATIC_DIR / "index.html"))

    return app
