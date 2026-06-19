"""FastAPI app factory for the local web visualizer."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agents.agent_responses import AnalysisInsights, Component
from codeboarding_web.component_data import changed_files, component_diff, component_files
from codeboarding_web.diagram import load_cytoscape, load_cytoscape_component
from codeboarding_web.events import EventBus, format_sse
from codeboarding_web.runner import AnalysisRunner
from codeboarding_web.state import RunBusyError, RunState
from codeboarding_web.watcher import RepoWatcher
from diagram_analysis.io_utils import parse_unified_analysis

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# Hostnames accepted when the server is bound to a loopback address. The app
# has no auth and POST /api/run spends real LLM tokens while the diff routes
# expose working-tree source, so a loopback bind rejects foreign Host headers
# to defend against DNS-rebinding from any site the user visits.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _find_component(
    analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights], component_id: str
) -> Component | None:
    """Search root and all sub-analyses for a component whose component_id matches."""
    for comp in analysis.components:
        if comp.component_id == component_id:
            return comp
    for sub in sub_analyses.values():
        for comp in sub.components:
            if comp.component_id == component_id:
                return comp
    return None


class RunRequest(BaseModel):
    """Body for POST /api/run."""

    scope: str = "full"
    base_ref: str = "HEAD~1"
    target_ref: str = "HEAD"


class WatchRequest(BaseModel):
    """Body for POST /api/watch."""

    enabled: bool


def create_app(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    depth_level: int = 1,
    watch: bool = False,
    bind_host: str | None = None,
) -> FastAPI:
    """Build and return the FastAPI application.

    When *bind_host* is a loopback address, a Host-header allowlist is
    installed (DNS-rebinding defense). A non-loopback bind (e.g. ``0.0.0.0``)
    is an explicit exposure choice and is left unguarded.
    """
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
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        bus.set_loop(asyncio.get_running_loop())
        stop = asyncio.Event()
        task = asyncio.create_task(RepoWatcher(repo_path, output_dir, on_change).run(stop))
        yield
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="CodeBoarding", lifespan=lifespan)
    if bind_host in _LOOPBACK_HOSTS:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(_LOOPBACK_HOSTS))
    app.state.runner = runner
    app.state.run_state = state
    app.state.bus = bus
    app.state.watch_enabled = watch

    @app.middleware("http")
    async def _no_cache_static(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

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
            "repo_path": str(repo_path.resolve()),
            "depth_level": runner.depth_level,
        }

    @app.get("/api/diagram.json")
    def diagram() -> JSONResponse:
        """Return Cytoscape elements for the current analysis, or 404."""
        elements = load_cytoscape(output_dir, project_name, repo_path)
        if elements is None:
            raise HTTPException(status_code=404, detail="no analysis yet")
        return JSONResponse(elements)

    @app.get("/api/diagram/{component_id}")
    def diagram_component(component_id: str) -> JSONResponse:
        """Return Cytoscape elements for a component sub-graph, or 404."""
        elements = load_cytoscape_component(output_dir, project_name, repo_path, component_id)
        if elements is None:
            raise HTTPException(status_code=404, detail="component not found")
        return JSONResponse(elements)

    @app.get("/api/component/{component_id}/diff")
    def component_diff_route(component_id: str) -> JSONResponse:
        """Return git diff patch for the files belonging to a component, or 404."""
        path = output_dir / "analysis.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="no analysis yet")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            root_analysis, sub_analyses = parse_unified_analysis(data)
        except Exception:
            raise HTTPException(status_code=404, detail="analysis not readable")
        comp = _find_component(root_analysis, sub_analyses, component_id)
        if comp is None:
            raise HTTPException(status_code=404, detail="component not found")
        changed = changed_files(repo_path)
        files = sorted(component_files(comp, repo_path) & changed)
        diff = component_diff(repo_path, files)
        return JSONResponse({"component_id": component_id, "files": files, "diff": diff})

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

    @app.post("/api/cancel")
    def cancel_run() -> dict:
        """Signal the current run to stop; no-op if idle."""
        if state.is_busy:
            runner.cancel()
            return {"cancelling": True}
        return {"cancelling": False}

    @app.post("/api/watch")
    def set_watch(req: WatchRequest) -> dict:
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
