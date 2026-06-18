# Standalone Web Visualizer (`codeboarding serve`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `codeboarding serve` command that runs a local FastAPI web app which renders the interactive Cytoscape diagram and streams live, per-component diagram + progress updates over SSE while a full or incremental analysis runs — preserving the user's viewport.

**Architecture:** A new additive `codeboarding_web/` package drives the existing analysis pipeline on a background thread. A `logging.Handler` taps the existing `traces` logger for step/token progress; a new optional `progress_callback` on `DiagramGenerator` fires after each intermediate `analysis.json` save so the web layer can re-read the file and push a fresh Cytoscape snapshot. The browser keeps one long-lived Cytoscape instance and applies snapshots in place (capture pan/zoom → add/remove/update → restore → highlight).

**Tech Stack:** Python ≥3.12, FastAPI + uvicorn (already deps), hand-rolled SSE via `StreamingResponse` (no new dep), vanilla JS + Cytoscape/Dagre (already used by the renderer).

## Global Constraints

- Python ≥3.12; format with Black, line length **120**.
- Type hints on all new code; **builtin generics** (`dict`/`list`/`set`), never `typing.Dict`/`List`/`Set`.
- No function-level imports; no `if TYPE_CHECKING` blocks.
- Terse docstrings: one-line summary; add a `Why:` line only when non-obvious. No diff-history narration.
- `snake_case` variables/functions; `PascalCase` classes.
- Test coverage ≥80%; verification commands: `uv run pytest --ignore=tests/integration`, `uv run mypy .`, `uv run black . --check`.
- No new runtime dependency may be added to `pyproject.toml`.
- Default bind `127.0.0.1`, default port `8050`. No auth (local single-user tool).
- Branch: `feat/standalone-web-visualizer` (fork `paulrobello/CodeBoarding`). Commit per task.

---

## File Structure

- `diagram_analysis/diagram_generator.py` — MODIFY: add optional `progress_callback` param + invoke after intermediate saves.
- `codeboarding_workflows/analysis.py` — MODIFY: thread `progress_callback` through `build_generator`, `run_full`, `run_incremental`.
- `codeboarding_web/__init__.py` — CREATE: package marker + `create_app` re-export.
- `codeboarding_web/state.py` — CREATE: `RunState`, `RunPhase`, busy guard.
- `codeboarding_web/diagram.py` — CREATE: read `analysis.json` → Cytoscape JSON (reuse renderer derivation).
- `codeboarding_web/events.py` — CREATE: `EventBus` (asyncio.Queue fan-in) + `TraceLogHandler` + SSE frame encoder.
- `codeboarding_web/runner.py` — CREATE: background-thread analysis driver wiring bus + callback.
- `codeboarding_web/app.py` — CREATE: FastAPI app factory + routes + static mount.
- `codeboarding_web/static/{index.html,app.js,app.css}` — CREATE: SPA shell.
- `codeboarding_cli/commands/serve_analysis.py` — CREATE: `add_arguments` + `run_from_args` (launch uvicorn).
- `main.py` — MODIFY: register `serve` subcommand + dispatch.
- `tests/web/test_*.py` — CREATE: unit tests for state, diagram, events, runner, app, generator seam.

---

## Task 1: Optional `progress_callback` seam in `DiagramGenerator`

**Files:**
- Modify: `diagram_analysis/diagram_generator.py` (`__init__` ~line 71-110; `_generate_subcomponents` ~line 428-434)
- Test: `tests/web/test_generator_callback.py`

**Interfaces:**
- Produces: `DiagramGenerator(..., progress_callback: Callable[[], None] | None = None)`. When set, it is called (best-effort, exceptions swallowed) after every intermediate `save_analysis` inside `_generate_subcomponents`. Default `None` → unchanged behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_generator_callback.py
from diagram_analysis.diagram_generator import DiagramGenerator


def test_progress_callback_defaults_to_none(tmp_path):
    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path,
        repo_name="demo",
        output_dir=tmp_path,
        depth_level=1,
        run_id="abc123",
        log_path=str(tmp_path),
    )
    assert gen.progress_callback is None


def test_progress_callback_is_stored(tmp_path):
    calls = []
    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path,
        repo_name="demo",
        output_dir=tmp_path,
        depth_level=1,
        run_id="abc123",
        log_path=str(tmp_path),
        progress_callback=lambda: calls.append(1),
    )
    gen.progress_callback()
    assert calls == [1]


def test_notify_progress_swallows_exceptions(tmp_path):
    def boom() -> None:
        raise RuntimeError("nope")

    gen = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path,
        repo_name="demo",
        output_dir=tmp_path,
        depth_level=1,
        run_id="abc123",
        log_path=str(tmp_path),
        progress_callback=boom,
    )
    gen._notify_progress()  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_generator_callback.py -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'progress_callback'` / `AttributeError`).

- [ ] **Step 3: Add the parameter, attribute, and notifier**

In `DiagramGenerator.__init__`, add `progress_callback: Callable[[], None] | None = None` as the last parameter and store it:

```python
        self.progress_callback = progress_callback
```

Add `from collections.abc import Callable` to the module's top imports if not already present (check first; do not duplicate).

Add a method on the class:

```python
    def _notify_progress(self) -> None:
        """Fire the optional progress callback; never let it break a run."""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback()
        except Exception:
            logger.exception("progress_callback raised; ignoring")
```

- [ ] **Step 4: Invoke after the intermediate save**

In `_generate_subcomponents`, immediately after the existing `save_analysis(...)` call inside the `if comp_name and sub_analysis:` block (~line 434), add:

```python
                            self._notify_progress()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_generator_callback.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add diagram_analysis/diagram_generator.py tests/web/test_generator_callback.py
git commit -m "feat(engine): optional progress_callback on DiagramGenerator"
```

---

## Task 2: Thread `progress_callback` through workflow builders

**Files:**
- Modify: `codeboarding_workflows/analysis.py` (`build_generator`, `run_full`, `run_incremental`)
- Test: `tests/web/test_analysis_callback.py`

**Interfaces:**
- Consumes: `DiagramGenerator(progress_callback=...)` from Task 1.
- Produces: `build_generator(..., progress_callback=None)`, `run_full(..., progress_callback=None)`, `run_incremental(..., progress_callback=None)` all forward the callback to the generator. Defaults preserve current behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_analysis_callback.py
import inspect
from codeboarding_workflows import analysis


def test_build_generator_forwards_callback(monkeypatch, tmp_path):
    captured = {}

    class FakeGen:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(analysis, "DiagramGenerator", FakeGen)
    cb = lambda: None
    analysis.build_generator(
        repo_name="demo",
        repo_path=tmp_path,
        output_dir=tmp_path,
        run_id="r1",
        log_path=str(tmp_path),
        depth_level=1,
        progress_callback=cb,
    )
    assert captured["progress_callback"] is cb


def test_run_full_accepts_callback():
    assert "progress_callback" in inspect.signature(analysis.run_full).parameters


def test_run_incremental_accepts_callback():
    assert "progress_callback" in inspect.signature(analysis.run_incremental).parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_analysis_callback.py -v`
Expected: FAIL (`progress_callback` not in signatures / not forwarded).

- [ ] **Step 3: Add the parameter to `build_generator`**

Add `progress_callback: Callable[[], None] | None = None` as the last parameter of `build_generator` and pass `progress_callback=progress_callback` into the `DiagramGenerator(...)` call. Add `from collections.abc import Callable` to the top imports.

- [ ] **Step 4: Forward through `run_full` and `run_incremental`**

Add `progress_callback: Callable[[], None] | None = None` as the last parameter of both `run_full` and `run_incremental`, and pass `progress_callback=progress_callback` into their `build_generator(...)` calls.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_analysis_callback.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add codeboarding_workflows/analysis.py tests/web/test_analysis_callback.py
git commit -m "feat(workflows): forward progress_callback to generator"
```

---

## Task 3: Run state machine

**Files:**
- Create: `codeboarding_web/__init__.py`, `codeboarding_web/state.py`
- Test: `tests/web/test_state.py`

**Interfaces:**
- Produces:
  - `class RunPhase(str, Enum)` with `IDLE`, `RUNNING`, `DONE`, `ERROR`.
  - `class RunState` with attrs `phase: RunPhase`, `run_id: str | None`, `scope: str | None`, `error: str | None`.
  - `RunState.begin(run_id: str, scope: str) -> None` (raises `RunBusyError` if already `RUNNING`).
  - `RunState.finish(error: str | None = None) -> None`.
  - `RunState.is_busy -> bool`.
  - `class RunBusyError(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_state.py
import pytest
from codeboarding_web.state import RunState, RunPhase, RunBusyError


def test_starts_idle():
    s = RunState()
    assert s.phase is RunPhase.IDLE
    assert not s.is_busy


def test_begin_marks_running():
    s = RunState()
    s.begin("r1", "full")
    assert s.is_busy
    assert s.phase is RunPhase.RUNNING
    assert s.run_id == "r1"
    assert s.scope == "full"


def test_begin_while_running_raises():
    s = RunState()
    s.begin("r1", "full")
    with pytest.raises(RunBusyError):
        s.begin("r2", "incremental")


def test_finish_success():
    s = RunState()
    s.begin("r1", "full")
    s.finish()
    assert s.phase is RunPhase.DONE
    assert s.error is None
    assert not s.is_busy


def test_finish_with_error():
    s = RunState()
    s.begin("r1", "full")
    s.finish(error="boom")
    assert s.phase is RunPhase.ERROR
    assert s.error == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_state.py -v`
Expected: FAIL (`ModuleNotFoundError: codeboarding_web`).

- [ ] **Step 3: Create the package + state**

```python
# codeboarding_web/__init__.py
"""Standalone local web visualizer for CodeBoarding analyses."""
```

```python
# codeboarding_web/state.py
"""Single-run state machine for the local web visualizer."""

from enum import Enum


class RunBusyError(RuntimeError):
    """Raised when a run is requested while another is in flight."""


class RunPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class RunState:
    """Tracks the single active analysis run (v1 allows one at a time)."""

    def __init__(self) -> None:
        self.phase: RunPhase = RunPhase.IDLE
        self.run_id: str | None = None
        self.scope: str | None = None
        self.error: str | None = None

    @property
    def is_busy(self) -> bool:
        return self.phase is RunPhase.RUNNING

    def begin(self, run_id: str, scope: str) -> None:
        if self.is_busy:
            raise RunBusyError("an analysis run is already in progress")
        self.phase = RunPhase.RUNNING
        self.run_id = run_id
        self.scope = scope
        self.error = None

    def finish(self, error: str | None = None) -> None:
        self.phase = RunPhase.ERROR if error else RunPhase.DONE
        self.error = error
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_state.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add codeboarding_web/__init__.py codeboarding_web/state.py tests/web/test_state.py
git commit -m "feat(web): single-run state machine"
```

---

## Task 4: Diagram adapter (`analysis.json` → Cytoscape JSON)

**Files:**
- Create: `codeboarding_web/diagram.py`
- Test: `tests/web/test_diagram.py`

**Interfaces:**
- Consumes: `output_generators.html.generate_cytoscape_data`, `diagram_analysis.io_utils` JSON on disk.
- Produces: `load_cytoscape(output_dir: Path, project: str) -> dict | None` — returns `{"elements":[...]}` for the overview graph, or `None` when no readable `analysis.json` exists. Uses the renderer's exact overview derivation: `root_expanded = set(sub_analyses.keys())`, `demo=False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_diagram.py
import json
from pathlib import Path
from codeboarding_web.diagram import load_cytoscape


def _write_analysis(output_dir: Path) -> None:
    # Minimal unified analysis.json: one root component, no sub-analyses.
    data = {
        "analysis": {
            "components": [
                {
                    "component_id": "1",
                    "name": "Core",
                    "description": "core",
                    "referenced_source_code": [],
                }
            ],
            "components_relations": [],
        },
        "sub_analyses": {},
        "metadata": {"depth_level": 1},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(json.dumps(data), encoding="utf-8")


def test_returns_none_when_absent(tmp_path):
    assert load_cytoscape(tmp_path, "demo") is None


def test_returns_elements_for_present_analysis(tmp_path):
    _write_analysis(tmp_path)
    result = load_cytoscape(tmp_path, "demo")
    assert result is not None
    ids = {e["data"]["id"] for e in result["elements"]}
    assert "Core" in ids


def test_returns_none_on_corrupt_json(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "analysis.json").write_text("{not json", encoding="utf-8")
    assert load_cytoscape(tmp_path, "demo") is None
```

> NOTE for implementer: confirm the real unified-analysis JSON shape with
> `diagram_analysis/io_utils.py:load_full_analysis` / `parse_unified_analysis`
> and `agents/agent_responses.py` before finalizing the fixture. Use
> `parse_unified_analysis` (the same parser `rendering.py` uses) to load, so
> the fixture must match what that parser expects. Adjust the fixture keys to
> match; the assertions on `load_cytoscape` behavior stay the same.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_diagram.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the adapter**

```python
# codeboarding_web/diagram.py
"""Adapt a saved analysis.json into Cytoscape elements for the overview graph."""

import json
import logging
from pathlib import Path

from diagram_analysis.io_utils import parse_unified_analysis
from output_generators.html import generate_cytoscape_data

logger = logging.getLogger(__name__)


def load_cytoscape(output_dir: Path, project: str) -> dict | None:
    """Read ``analysis.json`` from *output_dir* and return overview Cytoscape JSON.

    Returns None when the file is missing or unreadable (e.g. mid-write).
    Why: the writer re-saves during a run; a transient parse failure must not
    crash the SSE stream.
    """
    path = output_dir / "analysis.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        root_analysis, sub_analyses = parse_unified_analysis(data)
    except Exception:
        logger.debug("analysis.json not readable yet", exc_info=True)
        return None
    expanded = set(sub_analyses.keys())
    return generate_cytoscape_data(root_analysis, expanded, project, demo=False)
```

> Implementer: verify `parse_unified_analysis` is importable from
> `diagram_analysis.io_utils` (grep it). If it lives elsewhere, import from its
> real module — do not duplicate the parser.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_diagram.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add codeboarding_web/diagram.py tests/web/test_diagram.py
git commit -m "feat(web): analysis.json -> Cytoscape overview adapter"
```

---

## Task 5: Event bus + trace log handler + SSE encoder

**Files:**
- Create: `codeboarding_web/events.py`
- Test: `tests/web/test_events.py`

**Interfaces:**
- Produces:
  - `class Event` = `dict` alias used as `{"event": str, "data": dict}` (plain dicts; no class needed).
  - `class EventBus`:
    - `__init__(self, loop: asyncio.AbstractEventLoop)`
    - `subscribe() -> asyncio.Queue` / `unsubscribe(q)` 
    - `publish_threadsafe(event: str, data: dict) -> None` (callable from worker thread; uses `loop.call_soon_threadsafe`).
  - `class TraceLogHandler(logging.Handler)`: parses JSON `traces` records → `bus.publish_threadsafe(record_event, payload)`; attach to logger name `"traces"`.
  - `format_sse(event: str, data: dict) -> str` → `"event: <event>\ndata: <json>\n\n"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_events.py
import asyncio
import json
import logging
import pytest
from codeboarding_web.events import EventBus, TraceLogHandler, format_sse


def test_format_sse():
    out = format_sse("run_end", {"run_id": "r1"})
    assert out == 'event: run_end\ndata: {"run_id": "r1"}\n\n'


@pytest.mark.asyncio
async def test_publish_threadsafe_reaches_subscriber():
    loop = asyncio.get_running_loop()
    bus = EventBus(loop)
    q = bus.subscribe()
    # Simulate a worker-thread publish on the same loop.
    bus.publish_threadsafe("step_start", {"step": "x"})
    event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event == {"event": "step_start", "data": {"step": "x"}}


@pytest.mark.asyncio
async def test_trace_handler_publishes_parsed_records():
    loop = asyncio.get_running_loop()
    bus = EventBus(loop)
    q = bus.subscribe()
    handler = TraceLogHandler(bus)
    logger = logging.getLogger("traces")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.info(json.dumps({"event": "phase_change", "step": "code_generation"}))
        event = await asyncio.wait_for(q.get(), timeout=1.0)
    finally:
        logger.removeHandler(handler)
    assert event["event"] == "phase_change"
    assert event["data"]["step"] == "code_generation"
```

Add `pytest-asyncio` usage: the repo's dev deps must include it. Implementer: check `pyproject.toml` dev-dependencies for `pytest-asyncio`; if absent, instead write these two async tests using `asyncio.run(...)` wrappers inside sync test functions (no new dependency — Global Constraint). Prefer the `asyncio.run` form if unsure.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_events.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement events**

```python
# codeboarding_web/events.py
"""In-process event fan-in (worker thread -> asyncio queues) + SSE framing."""

import asyncio
import json
import logging


def format_sse(event: str, data: dict) -> str:
    """Encode one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class EventBus:
    """Fan-in from a worker thread to any number of asyncio subscribers."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish_threadsafe(self, event: str, data: dict) -> None:
        """Publish from any thread; marshals onto the event loop."""
        self._loop.call_soon_threadsafe(self._publish, event, data)

    def _publish(self, event: str, data: dict) -> None:
        message = {"event": event, "data": data}
        for q in list(self._subscribers):
            q.put_nowait(message)


class TraceLogHandler(logging.Handler):
    """Forward JSON ``traces`` logger records onto an :class:`EventBus`."""

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return
        event = payload.get("event", "trace")
        self._bus.publish_threadsafe(event, payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_events.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add codeboarding_web/events.py tests/web/test_events.py
git commit -m "feat(web): event bus, trace log handler, SSE encoder"
```

---

## Task 6: Background analysis runner

**Files:**
- Create: `codeboarding_web/runner.py`
- Test: `tests/web/test_runner.py`

**Interfaces:**
- Consumes: `RunState`/`RunBusyError` (Task 3), `EventBus`/`TraceLogHandler` (Task 5), `load_cytoscape` (Task 4), workflow `run_full`/`run_incremental` (Task 2), `local_source`, `run_analysis_pipeline`, `monitor_execution`.
- Produces:
  - `class AnalysisRunner(repo_path: Path, output_dir: Path, project_name: str, state: RunState, bus: EventBus)`.
  - `start(self, scope: str, base_ref: str = "HEAD~1", target_ref: str = "HEAD") -> str` — validates not busy (raises `RunBusyError`), generates a `run_id`, calls `state.begin`, spawns a daemon thread running `_run`, returns `run_id`.
  - `_run(...)` (thread body): attaches `TraceLogHandler` to `"traces"`; builds a `progress_callback` that publishes `diagram_delta` with `load_cytoscape(...)`; drives the pipeline; publishes `run_end`/`run_error`; detaches handler and calls `state.finish` in `finally`.

- [ ] **Step 1: Write the failing test** (driver mocked — no real LLM/pipeline)

```python
# tests/web/test_runner.py
import asyncio
import pytest
from pathlib import Path
from codeboarding_web.state import RunState, RunBusyError
from codeboarding_web.events import EventBus
from codeboarding_web import runner as runner_mod
from codeboarding_web.runner import AnalysisRunner


def _make(tmp_path, bus):
    return AnalysisRunner(
        repo_path=tmp_path,
        output_dir=tmp_path,
        project_name="demo",
        state=RunState(),
        bus=bus,
    )


def test_start_rejects_unknown_scope(tmp_path):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    with pytest.raises(ValueError):
        r.start("bogus")
    loop.close()


def test_start_when_busy_raises(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    r.state.begin("existing", "full")
    with pytest.raises(RunBusyError):
        r.start("full")
    loop.close()


def test_start_invokes_driver_and_finishes(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    bus = EventBus(loop)
    r = _make(tmp_path, bus)
    called = {}

    def fake_drive(scope, run_id, base_ref, target_ref, progress_callback):
        called["scope"] = scope
        progress_callback()  # exercise the callback path

    monkeypatch.setattr(r, "_drive_pipeline", fake_drive)
    run_id = r.start("full")
    r._thread.join(timeout=5)
    assert called["scope"] == "full"
    assert r.state.phase.value in ("done", "error")
    assert run_id
    loop.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_runner.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the runner**

```python
# codeboarding_web/runner.py
"""Drive the analysis pipeline on a background thread and stream events."""

import logging
import threading
import uuid
from pathlib import Path

from codeboarding_web.diagram import load_cytoscape
from codeboarding_web.events import EventBus, TraceLogHandler
from codeboarding_web.state import RunState
from codeboarding_workflows.analysis import run_full, run_incremental
from codeboarding_workflows.orchestration import run_analysis_pipeline
from codeboarding_workflows.sources import SourceContext, local_source
from diagram_analysis import RunContext
from monitoring import monitor_execution

logger = logging.getLogger(__name__)

_SCOPES = {"full", "incremental"}


class AnalysisRunner:
    """Owns the single background analysis run for one served repo."""

    def __init__(
        self,
        repo_path: Path,
        output_dir: Path,
        project_name: str,
        state: RunState,
        bus: EventBus,
    ) -> None:
        self.repo_path = repo_path
        self.output_dir = output_dir
        self.project_name = project_name
        self.state = state
        self.bus = bus
        self._thread: threading.Thread | None = None

    def start(self, scope: str, base_ref: str = "HEAD~1", target_ref: str = "HEAD") -> str:
        if scope not in _SCOPES:
            raise ValueError(f"unknown scope: {scope!r}")
        run_id = uuid.uuid4().hex[:8]
        self.state.begin(run_id, scope)  # raises RunBusyError if busy
        self._thread = threading.Thread(
            target=self._run,
            args=(scope, run_id, base_ref, target_ref),
            daemon=True,
        )
        self._thread.start()
        return run_id

    def _progress_callback(self) -> None:
        elements = load_cytoscape(self.output_dir, self.project_name)
        if elements is not None:
            self.bus.publish_threadsafe("diagram_delta", elements)

    def _run(self, scope: str, run_id: str, base_ref: str, target_ref: str) -> None:
        handler = TraceLogHandler(self.bus)
        traces_logger = logging.getLogger("traces")
        traces_logger.addHandler(handler)
        error: str | None = None
        try:
            self._drive_pipeline(scope, run_id, base_ref, target_ref, self._progress_callback)
        except Exception as exc:
            error = str(exc)
            logger.exception("analysis run failed")
        finally:
            traces_logger.removeHandler(handler)
            self.state.finish(error=error)
            if error:
                self.bus.publish_threadsafe("run_error", {"run_id": run_id, "error": error})
            else:
                self.bus.publish_threadsafe("run_end", {"run_id": run_id})

    def _drive_pipeline(self, scope, run_id, base_ref, target_ref, progress_callback) -> None:
        def doc_scope(src: SourceContext, run_context: RunContext) -> None:
            if scope == "full":
                run_full(
                    repo_name=src.project_name,
                    repo_path=src.repo_path,
                    output_dir=src.artifact_dir,
                    depth_level=1,
                    run_id=run_context.run_id,
                    log_path=run_context.log_path,
                    progress_callback=progress_callback,
                )
            else:
                run_incremental(
                    repo_path=src.repo_path,
                    output_dir=src.artifact_dir,
                    project_name=src.project_name,
                    run_id=run_context.run_id,
                    log_path=run_context.log_path,
                    base_ref=base_ref,
                    target_ref=target_ref,
                    progress_callback=progress_callback,
                )

        run_analysis_pipeline(
            source=local_source(
                repo_path=self.repo_path,
                project_name=self.project_name,
                artifact_dir=self.output_dir,
            ),
            scope=doc_scope,
        )
```

> Implementer: confirm `monitor_execution` is needed here or whether
> `run_full`/`run_incremental` already wrap monitoring. The trace events used
> for progress come from the `@trace` decorators on agents, which fire
> regardless. Keep the import only if you actually call it; otherwise drop it to
> satisfy "no unused imports" / lint. Mirror `full_analysis._run_local` for the
> exact `local_source` / `RunContext` wiring and adjust if signatures differ.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_runner.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add codeboarding_web/runner.py tests/web/test_runner.py
git commit -m "feat(web): background analysis runner"
```

---

## Task 7: FastAPI app + routes

**Files:**
- Create: `codeboarding_web/app.py`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `RunState`, `AnalysisRunner`, `EventBus`, `load_cytoscape`.
- Produces: `create_app(repo_path: Path, output_dir: Path, project_name: str, loop: asyncio.AbstractEventLoop | None = None) -> FastAPI`. Routes: `GET /api/status`, `GET /api/diagram.json`, `POST /api/run`, `GET /api/events`, `GET /` + static mount at `/static`. Re-export `create_app` from `codeboarding_web/__init__.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_app.py
import json
from pathlib import Path
from fastapi.testclient import TestClient
from codeboarding_web.app import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(repo_path=tmp_path, output_dir=tmp_path, project_name="demo"))


def test_status_idle_no_baseline(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "idle"
    assert body["has_baseline"] is False


def test_diagram_json_404_when_absent(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/diagram.json").status_code == 404


def test_run_rejects_bad_scope(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/run", json={"scope": "nope"})
    assert r.status_code == 422 or r.status_code == 400


def test_run_conflict_when_busy(tmp_path, monkeypatch):
    app = create_app(repo_path=tmp_path, output_dir=tmp_path, project_name="demo")
    c = TestClient(app)
    # Force busy by stubbing the runner's start to raise RunBusyError.
    from codeboarding_web.state import RunBusyError
    monkeypatch.setattr(app.state.runner, "start", lambda *a, **k: (_ for _ in ()).throw(RunBusyError()))
    r = c.post("/api/run", json={"scope": "full"})
    assert r.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the app**

```python
# codeboarding_web/app.py
"""FastAPI app factory for the local web visualizer."""

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from codeboarding_web.diagram import load_cytoscape
from codeboarding_web.events import EventBus, format_sse
from codeboarding_web.runner import AnalysisRunner
from codeboarding_web.state import RunBusyError, RunState

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    scope: str = "full"
    base_ref: str = "HEAD~1"
    target_ref: str = "HEAD"


def create_app(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    loop: asyncio.AbstractEventLoop | None = None,
) -> FastAPI:
    app = FastAPI(title="CodeBoarding")
    state = RunState()
    event_loop = loop or asyncio.get_event_loop()
    bus = EventBus(event_loop)
    runner = AnalysisRunner(repo_path, output_dir, project_name, state, bus)

    app.state.runner = runner
    app.state.run_state = state
    app.state.bus = bus

    @app.get("/api/status")
    def status() -> dict:
        return {
            "phase": state.phase.value,
            "run_id": state.run_id,
            "scope": state.scope,
            "error": state.error,
            "project": project_name,
            "has_baseline": (output_dir / "analysis.json").exists(),
        }

    @app.get("/api/diagram.json")
    def diagram() -> JSONResponse:
        elements = load_cytoscape(output_dir, project_name)
        if elements is None:
            raise HTTPException(status_code=404, detail="no analysis yet")
        return JSONResponse(elements)

    @app.post("/api/run")
    def run(req: RunRequest) -> dict:
        if req.scope not in {"full", "incremental"}:
            raise HTTPException(status_code=400, detail="scope must be full or incremental")
        try:
            run_id = runner.start(req.scope, req.base_ref, req.target_ref)
        except RunBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id, "scope": req.scope}

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        q = bus.subscribe()

        async def stream():
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
        def index() -> StreamingResponse:
            from fastapi.responses import FileResponse

            return FileResponse(str(_STATIC_DIR / "index.html"))

    return app
```

Update `codeboarding_web/__init__.py`:

```python
"""Standalone local web visualizer for CodeBoarding analyses."""

from codeboarding_web.app import create_app

__all__ = ["create_app"]
```

> Implementer: the top-level `from fastapi.responses import FileResponse` should
> move to the module's top imports (Global Constraint: no function-level
> imports). It is inlined here only to keep the index route guarded by the
> static-dir check; hoist it and guard the route registration instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add codeboarding_web/app.py codeboarding_web/__init__.py tests/web/test_app.py
git commit -m "feat(web): FastAPI app, routes, SSE endpoint"
```

---

## Task 8: `serve` CLI command + `main.py` wiring

**Files:**
- Create: `codeboarding_cli/commands/serve_analysis.py`
- Modify: `main.py` (`_SUBCOMMANDS`, `build_parser`, `main` dispatch)
- Test: `tests/web/test_serve_cli.py`

**Interfaces:**
- Consumes: `create_app`, `resolve_local_run_paths`, `bootstrap_environment`.
- Produces: `serve_analysis.add_arguments(subparsers, parents)`, `serve_analysis.run_from_args(args, parser)`. New args: `--host` (default `127.0.0.1`), `--port` (default `8050`), `--no-open`.

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_serve_cli.py
import argparse
from pathlib import Path
from codeboarding_cli.commands import serve_analysis


def test_add_arguments_registers_serve():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--local", type=Path)
    serve_analysis.add_arguments(subparsers, parents=[shared])
    args = parser.parse_args(["serve", "--local", "/tmp/x", "--port", "9999"])
    assert args.command == "serve"
    assert args.port == 9999
    assert args.host == "127.0.0.1"


def test_serve_in_main_subcommands():
    import main
    assert "serve" in main._SUBCOMMANDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_serve_cli.py -v`
Expected: FAIL (`ModuleNotFoundError` / `serve` not in `_SUBCOMMANDS`).

- [ ] **Step 3: Implement the command**

```python
# codeboarding_cli/commands/serve_analysis.py
"""`codeboarding serve` — local web visualizer with live progress streaming."""

import argparse
import logging
import threading
import webbrowser

import uvicorn

from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_web import create_app

logger = logging.getLogger(__name__)


def add_arguments(subparsers: argparse._SubParsersAction, parents: list[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "serve",
        parents=parents,
        help="Serve an interactive, live-updating diagram for a local repository.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8050, help="Port to bind (default: 8050)")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser tab")
    parser.add_argument("--depth-level", type=int, default=1, help="Depth level (default: 1)")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.local is None:
        parser.error("serve requires --local <path>")
    run_paths = resolve_local_run_paths(args)
    run_paths.output_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_environment(run_paths.output_dir, args.binary_location)

    app = create_app(
        repo_path=run_paths.repo_path,
        output_dir=run_paths.output_dir,
        project_name=run_paths.project_name,
    )

    url = f"http://{args.host}:{args.port}/"
    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    logger.info("Serving CodeBoarding visualizer at %s", url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
```

In `main.py`:
- Add `"serve"` to `_SUBCOMMANDS`.
- Import `serve_analysis` alongside the other command imports.
- In `build_parser`, call `serve_analysis.add_arguments(subparsers, parents=[shared])`.
- In `main`, add dispatch before the `full` fallthrough:

```python
    if args.command == "serve":
        serve_analysis.run_from_args(args, parser)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_serve_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Verify the command is wired end-to-end (manual smoke)**

Run: `uv run codeboarding serve --help`
Expected: serve help text with `--host`, `--port`, `--no-open`.

- [ ] **Step 6: Commit**

```bash
git add codeboarding_cli/commands/serve_analysis.py main.py tests/web/test_serve_cli.py
git commit -m "feat(cli): add serve subcommand"
```

---

## Task 9: SPA frontend (shell + live diagram + progress)

**Files:**
- Create: `codeboarding_web/static/index.html`, `codeboarding_web/static/app.css`, `codeboarding_web/static/app.js`
- Verify: in-browser (no unit test; this is DOM + Cytoscape glue)

**Interfaces:**
- Consumes the routes from Task 7: `GET /api/status`, `GET /api/diagram.json`, `POST /api/run`, `GET /api/events` (SSE events `step_start`, `step_end`, `phase_change`, `diagram_delta`, `run_end`, `run_error`).

- [ ] **Step 1: Create `index.html`**

```html
<!-- codeboarding_web/static/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CodeBoarding</title>
  <link rel="stylesheet" href="/static/app.css" />
  <script src="https://unpkg.com/dagre@0.8.5/dist/dagre.min.js"></script>
  <script src="https://unpkg.com/cytoscape@3.23.0/dist/cytoscape.min.js"></script>
  <script src="https://unpkg.com/cytoscape-dagre@2.4.0/cytoscape-dagre.js"></script>
</head>
<body>
  <header>
    <span id="project"></span>
    <span id="phase" class="phase idle">idle</span>
    <select id="scope">
      <option value="full">Full</option>
      <option value="incremental">Incremental</option>
    </select>
    <button id="run">Run analysis</button>
  </header>
  <main>
    <div id="cy"></div>
    <aside id="log"></aside>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `app.css`** (Dark-mode palette per user prefs: bg `#1E1E1E`, text `#E6E6E6`, info `#2196F3`, success `#4CAF50`, error `#F44336`)

```css
/* codeboarding_web/static/app.css */
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, sans-serif; background: #1E1E1E; color: #E6E6E6; }
header { display: flex; gap: .75rem; align-items: center; padding: .5rem .75rem; border-bottom: 1px solid #333; }
#project { font-weight: 600; }
.phase { padding: .1rem .5rem; border-radius: .25rem; font-size: .8rem; }
.phase.idle { background: #333; }
.phase.running { background: #2196F3; }
.phase.done { background: #4CAF50; }
.phase.error { background: #F44336; }
button, select { background: #2a2a2a; color: #E6E6E6; border: 1px solid #444; border-radius: .25rem; padding: .35rem .6rem; }
main { display: flex; height: calc(100vh - 49px); }
#cy { flex: 1; }
#log { width: 320px; border-left: 1px solid #333; overflow-y: auto; padding: .5rem; font-size: .8rem; font-family: ui-monospace, monospace; }
.log-line { padding: .15rem 0; border-bottom: 1px solid #2a2a2a; }
.node-flash { transition: background-color .1s; }
```

- [ ] **Step 3: Create `app.js`** (persistent Cytoscape; viewport-preserving in-place apply)

```javascript
// codeboarding_web/static/app.js
const STYLE = [
  { selector: 'node', style: { 'label': 'data(label)', 'color': '#E6E6E6', 'background-color': '#2196F3',
      'text-valign': 'center', 'text-halign': 'center', 'font-size': 10, 'width': 'label', 'padding': '8px' } },
  { selector: 'node.changed', style: { 'background-color': '#4CAF50', 'border-color': '#4CAF50', 'border-width': 3 } },
  { selector: 'edge', style: { 'width': 1.5, 'line-color': '#555', 'target-arrow-color': '#555',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'label': 'data(label)', 'font-size': 7, 'color': '#888' } },
];

const cy = cytoscape({ container: document.getElementById('cy'), elements: [], style: STYLE });
let hasLaidOut = false;

function logLine(text) {
  const el = document.createElement('div');
  el.className = 'log-line';
  el.textContent = text;
  const log = document.getElementById('log');
  log.prepend(el);
}

function setPhase(phase) {
  const el = document.getElementById('phase');
  el.textContent = phase;
  el.className = 'phase ' + phase;
}

// In-place apply: preserve pan/zoom, add/remove/update, highlight new/changed nodes.
function applyElements(elements) {
  const pan = cy.pan();
  const zoom = cy.zoom();
  const incoming = new Map(elements.map((e) => [e.data.id, e]));
  const existing = new Set(cy.elements().map((e) => e.id()));

  cy.elements().forEach((e) => { if (!incoming.has(e.id())) e.remove(); });

  const added = [];
  incoming.forEach((e, id) => {
    if (existing.has(id)) {
      cy.getElementById(id).data(e.data);
    } else {
      const el = cy.add(e);
      if (el.isNode()) added.push(el);
    }
  });

  if (!hasLaidOut) {
    cy.layout({ name: 'dagre', rankDir: 'LR' }).run();
    cy.fit(undefined, 30);
    hasLaidOut = true;
  } else if (added.length) {
    // Lay out only new nodes; lock existing positions; keep viewport.
    const locked = cy.nodes().difference(cy.collection(added));
    locked.lock();
    cy.layout({ name: 'dagre', rankDir: 'LR', fit: false }).run();
    locked.unlock();
    cy.pan(pan);
    cy.zoom(zoom);
  }

  added.forEach((n) => {
    n.addClass('changed');
    setTimeout(() => n.removeClass('changed'), 1500);
  });
}

async function loadDiagram() {
  const res = await fetch('/api/diagram.json');
  if (res.ok) applyElements((await res.json()).elements);
}

async function refreshStatus() {
  const s = await (await fetch('/api/status')).json();
  document.getElementById('project').textContent = s.project;
  setPhase(s.phase);
  if (s.has_baseline) loadDiagram();
}

function connectEvents() {
  const src = new EventSource('/api/events');
  src.addEventListener('step_start', (e) => logLine('▶ ' + JSON.parse(e.data).step));
  src.addEventListener('step_end', (e) => logLine('✓ ' + JSON.parse(e.data).step));
  src.addEventListener('phase_change', (e) => logLine('— ' + JSON.parse(e.data).step));
  src.addEventListener('diagram_delta', (e) => applyElements(JSON.parse(e.data).elements));
  src.addEventListener('run_end', () => { setPhase('done'); loadDiagram(); });
  src.addEventListener('run_error', (e) => { setPhase('error'); logLine('ERROR: ' + JSON.parse(e.data).error); });
}

document.getElementById('run').addEventListener('click', async () => {
  const scope = document.getElementById('scope').value;
  const res = await fetch('/api/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope }),
  });
  if (res.status === 409) { logLine('A run is already in progress.'); return; }
  if (res.status === 400) { logLine('Invalid scope.'); return; }
  setPhase('running');
});

refreshStatus();
connectEvents();
```

- [ ] **Step 4: In-browser verification**

Run (in a repo that already has a `.codeboarding/analysis.json`, e.g. this repo):
```bash
uv run codeboarding serve --local /Users/probello/Repos/CodeBoarding
```
Verify with agentchrome (per global browser rules):
- Page loads at `http://127.0.0.1:8050/`, existing diagram renders.
- Click "Run analysis" (full): phase chip turns blue, log lines stream in, nodes appear/refresh **without the viewport jumping**, chip turns green on completion.
- Pan/zoom mid-run, confirm a subsequent `diagram_delta` does not reset the view.

- [ ] **Step 5: Commit**

```bash
git add codeboarding_web/static/
git commit -m "feat(web): SPA shell with live, viewport-preserving diagram"
```

---

## Task 10: Verification + docs

**Files:**
- Modify: `README.md` (add a "Local web visualizer" section)
- Verify: full project gates

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest --cov=. --cov-report=term --cov-fail-under=80 --ignore=tests/integration`
Expected: PASS, coverage ≥80%.

- [ ] **Step 2: Type-check and format**

Run: `uv run mypy .` then `uv run black . --check`
Expected: both clean. Fix any issues (e.g. hoist inlined imports, add annotations).

- [ ] **Step 3: Add README section**

Add under the usage docs:

```markdown
### Local web visualizer (`codeboarding serve`)

Run an interactive, live-updating diagram for a local repository:

```bash
codeboarding serve --local /path/to/repo
```

This starts a local web app at `http://127.0.0.1:8050/` and opens your browser.
If an analysis already exists it renders immediately; click **Run analysis**
(full or incremental) to regenerate it, watching components and progress stream
in live while your pan/zoom is preserved. Flags: `--host`, `--port` (default
`8050`), `--no-open`.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document codeboarding serve web visualizer"
```

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin feat/standalone-web-visualizer
gh pr create --base main --head paulrobello:feat/standalone-web-visualizer \
  --title "feat: standalone local web visualizer (codeboarding serve)" \
  --body "WHAT: ... WHY: ... HOW: ..."  # fill WHAT/WHY/HOW per CONTRIBUTING
```

> Note: PR base is the upstream `CodeBoarding/CodeBoarding` `main`. Confirm with
> the user before opening a PR against upstream vs. keeping it on the fork.

---

## Self-Review Notes

- **Spec coverage:** §3 package layout → Tasks 3-9; §4 routes → Task 7; §5.1 trace progress → Task 5; §5.2 callback seam → Tasks 1-2,6; §5.3 in-place apply → Task 9; §6 lifecycle/concurrency → Tasks 3,6,7; §7 testing → each task's tests + Task 10; §8 constraints → Global Constraints; CLI (§3) → Task 8.
- **Type consistency:** `progress_callback: Callable[[], None] | None` is identical across Tasks 1, 2, 6. `load_cytoscape(output_dir, project) -> dict | None` consistent across Tasks 4, 6, 7. `EventBus.publish_threadsafe(event, data)` / `format_sse(event, data)` consistent across Tasks 5, 7. `RunState` API consistent across Tasks 3, 6, 7.
- **Known verification points flagged for implementer** (not placeholders — concrete checks): exact unified-analysis JSON fixture shape (Task 4), `parse_unified_analysis` import location (Task 4), `pytest-asyncio` availability vs. `asyncio.run` fallback (Task 5), whether `monitor_execution` is called in the runner (Task 6), hoisting the `FileResponse` import (Task 7), PR base choice (Task 10).
```
