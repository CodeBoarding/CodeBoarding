# Watch-on-Save Auto Re-Analysis — Implementation Plan

> Increment on `feat/standalone-web-visualizer`. Adds optional file-watching that auto-triggers incremental re-analysis (working-tree diff) on source changes, reusing the existing AnalysisRunner/SSE/diagram-delta path.

**Goal:** When enabled, saving a source file in the served repo auto-runs an incremental analysis and streams the updated diagram — no button click.

**Architecture:** A `RepoWatcher` (watchfiles `awatch`) runs as an asyncio task in the FastAPI lifespan. On a debounced source-file change it invokes an `on_change` orchestrator that — if watching is enabled, a baseline exists, and no run is in flight — calls `runner.start("incremental", base_ref="HEAD", target_ref="")` (working-tree diff incl. untracked) and publishes a `watch_triggered` SSE event. Toggleable via CLI flag and a header switch.

**Tech Stack:** watchfiles 1.2.0 (added), existing FastAPI/asyncio/EventBus/AnalysisRunner.

## Global Constraints
- Black 120; type hints; builtin generics; no function-level imports; no `if TYPE_CHECKING`; terse docstrings.
- MUST exclude `.git/` (DefaultFilter already does) AND the analysis **output_dir** from watching — the run writes `analysis.json` there; watching it would infinite-loop.
- `uv run pytest --ignore=tests/integration`, `uv run mypy .`, `uv run black . --check` clean.
- Backward compatible: watching defaults follow the CLI flag; the existing button/run flow is unchanged.

## Files
- Create: `codeboarding_web/watcher.py` — `RepoWatcher` (filter + async run loop).
- Modify: `codeboarding_web/app.py` — `create_app(watch=...)`, `app.state.watch_enabled`, `POST /api/watch`, `watch_enabled` in `/api/status`, lifespan task + `on_change` orchestrator.
- Modify: `codeboarding_cli/commands/serve_analysis.py` — `--watch/--no-watch` flag → `create_app(watch=...)`.
- Modify: `codeboarding_web/static/{index.html,app.css,app.js}` — header toggle + `watch_triggered` log line + reflect watch state.
- Test: `tests/web/test_watcher.py`, extend `tests/web/test_app.py`.

---

## Task W1: RepoWatcher (filter + watch loop)

**Files:** Create `codeboarding_web/watcher.py`; Test `tests/web/test_watcher.py`.

**Interfaces:**
- Produces: `class RepoWatcher(repo_path: Path, output_dir: Path, on_change: Callable[[], None])`.
  - `_should_watch(path: str) -> bool` — True only for source files (suffix in `SOURCE_EXTENSION_TO_LANGUAGE`) NOT under `output_dir` and NOT under `.git`.
  - `async run(stop_event)` — `async for changes in awatch(repo_path, watch_filter=..., stop_event=stop_event): on_change()`.

- [ ] **Step 1: failing test** `tests/web/test_watcher.py`:
```python
from pathlib import Path
from codeboarding_web.watcher import RepoWatcher


def _w(tmp_path):
    return RepoWatcher(repo_path=tmp_path, output_dir=tmp_path / ".codeboarding", on_change=lambda: None)


def test_watches_source_file(tmp_path):
    assert _w(tmp_path)._should_watch(str(tmp_path / "pkg" / "mod.py")) is True


def test_ignores_output_dir(tmp_path):
    w = _w(tmp_path)
    assert w._should_watch(str(tmp_path / ".codeboarding" / "analysis.json")) is False


def test_ignores_non_source(tmp_path):
    assert _w(tmp_path)._should_watch(str(tmp_path / "README.md")) is False


def test_ignores_git_dir(tmp_path):
    assert _w(tmp_path)._should_watch(str(tmp_path / ".git" / "index")) is False
```

- [ ] **Step 2: run → fails** `uv run pytest tests/web/test_watcher.py -v` (ModuleNotFoundError).

- [ ] **Step 3: implement** `codeboarding_web/watcher.py`:
```python
"""Watch a repo's source tree and fire a callback on debounced source changes."""

import logging
from collections.abc import Callable
from pathlib import Path

from watchfiles import awatch
from watchfiles.main import Change

from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE

logger = logging.getLogger(__name__)


class RepoWatcher:
    """Fire *on_change* when a source file under *repo_path* changes."""

    def __init__(self, repo_path: Path, output_dir: Path, on_change: Callable[[], None]) -> None:
        self.repo_path = repo_path
        self.output_dir = output_dir.resolve()
        self.on_change = on_change

    def _should_watch(self, path: str) -> bool:
        """True for source files outside the output dir and .git."""
        p = Path(path).resolve()
        if self.output_dir in p.parents or p == self.output_dir:
            return False
        if ".git" in p.parts:
            return False
        return p.suffix.lower() in SOURCE_EXTENSION_TO_LANGUAGE

    def _watch_filter(self, change: Change, path: str) -> bool:
        return self._should_watch(path)

    async def run(self, stop_event) -> None:
        """Watch until *stop_event* is set, firing on_change per debounced batch."""
        async for _ in awatch(self.repo_path, watch_filter=self._watch_filter, stop_event=stop_event):
            try:
                self.on_change()
            except Exception:
                logger.exception("watch on_change failed")
```

- [ ] **Step 4: run → passes** `uv run pytest tests/web/test_watcher.py -v` (4 passed).
- [ ] **Step 5: commit** `git add codeboarding_web/watcher.py tests/web/test_watcher.py && git commit -m "feat(web): RepoWatcher source-file watcher"`

---

## Task W2: app wiring (toggle, status, lifespan task, orchestrator)

**Files:** Modify `codeboarding_web/app.py`; Test: extend `tests/web/test_app.py`.

**Interfaces:**
- `create_app(repo_path, output_dir, project_name, depth_level=1, watch=False) -> FastAPI`.
- `app.state.watch_enabled: bool` (init = `watch`).
- `GET /api/status` payload gains `watch_enabled: bool`.
- `POST /api/watch` body `{enabled: bool}` → sets `app.state.watch_enabled`, returns `{watch_enabled}`.
- lifespan: bind loop (existing) + create `asyncio.Event()` stop + spawn `RepoWatcher(...).run(stop)` task; on shutdown set stop + cancel task.
- `on_change()` orchestrator (module-level helper or closure): if not `app.state.watch_enabled` → return; if `state.is_busy` → return; if no `output_dir/analysis.json` → publish `watch_triggered` with `{"status":"no_baseline"}` and return; else publish `watch_triggered` `{"status":"running"}` and `runner.start("incremental", base_ref="HEAD", target_ref="")` (catch `RunBusyError` → return).

- [ ] **Step 1: failing tests** add to `tests/web/test_app.py`:
```python
def test_status_includes_watch_enabled(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/status").json()["watch_enabled"] is False


def test_watch_toggle(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/watch", json={"enabled": True})
    assert r.status_code == 200 and r.json()["watch_enabled"] is True
    assert c.get("/api/status").json()["watch_enabled"] is True
```
(Use the existing `_client` helper / create_app — default `watch=False`.)

- [ ] **Step 2: run → fails** `uv run pytest tests/web/test_app.py -k "watch" -v`.

- [ ] **Step 3: implement** in `codeboarding_web/app.py`:
  - Add imports at top: `import asyncio` (present), `from codeboarding_web.watcher import RepoWatcher`.
  - `create_app` signature gains `watch: bool = False`; set `app.state.watch_enabled = watch` (store on app.state alongside runner/bus/state).
  - Add a `WatchRequest(BaseModel)` with `enabled: bool`.
  - Add `on_change()` (closure over `state`, `runner`, `bus`, `app`, `output_dir`) per the orchestrator contract above. `watch_triggered` events: `bus.publish_threadsafe("watch_triggered", {"status": ...})`.
  - In `lifespan`: after `bus.set_loop(...)`, create `stop = asyncio.Event()`; `task = asyncio.create_task(RepoWatcher(repo_path, output_dir, on_change).run(stop))`; `yield`; then `stop.set()` and `task.cancel()` (await with suppressed `CancelledError`).
  - `GET /api/status`: add `"watch_enabled": app.state.watch_enabled` (read from app.state, not the closure var, so the toggle is reflected).
  - `POST /api/watch`: set `app.state.watch_enabled = req.enabled`; return `{"watch_enabled": app.state.watch_enabled}`.

- [ ] **Step 4: run → passes** `uv run pytest tests/web/test_app.py -q` (all pass). Note: the lifespan watcher only runs under `with TestClient(...)`; the non-context `_client` helper won't spawn it, so these toggle tests don't start a real watcher.
- [ ] **Step 5: mypy/black** `uv run mypy codeboarding_web/ tests/web/` and `uv run black codeboarding_web/ tests/web/ --check`.
- [ ] **Step 6: commit** `git add codeboarding_web/app.py tests/web/test_app.py && git commit -m "feat(web): watch toggle, status, lifespan watcher task"`

---

## Task W3: CLI flag + SPA toggle + verify

**Files:** Modify `codeboarding_cli/commands/serve_analysis.py`, `codeboarding_web/static/{index.html,app.css,app.js}`; Test: extend `tests/web/test_serve_cli.py`.

- [ ] **Step 1: CLI flag** in `serve_analysis.add_arguments`: add a mutually-exclusive-ish pair via `argparse.BooleanOptionalAction`:
```python
    parser.add_argument("--watch", action=argparse.BooleanOptionalAction, default=True,
                        help="Auto re-analyze on source changes (default: on)")
```
In `run_from_args`, pass `watch=args.watch` to `create_app(...)`.

- [ ] **Step 2: test** in `tests/web/test_serve_cli.py`:
```python
def test_watch_flag_defaults_on_and_can_disable():
    import argparse
    from pathlib import Path
    from codeboarding_cli.commands import serve_analysis
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--local", type=Path)
    serve_analysis.add_arguments(sub, parents=[shared])
    assert parser.parse_args(["serve", "--local", "/tmp/x"]).watch is True
    assert parser.parse_args(["serve", "--local", "/tmp/x", "--no-watch"]).watch is False
```
Run `uv run pytest tests/web/test_serve_cli.py -q`.

- [ ] **Step 3: SPA** — in `index.html` header add a watch toggle:
```html
    <label class="watch"><input type="checkbox" id="watch" /> Watch</label>
```
In `app.css` add a `.watch` style (inherit dark palette). In `app.js`:
  - On load, set `#watch.checked` from `/api/status` `watch_enabled`.
  - On toggle change: `POST /api/watch {enabled: checkbox.checked}`.
  - SSE: add listener `watch_triggered` → log `change detected → re-analyzing` (or `no baseline — run full first` when `data.status === "no_baseline"`).

- [ ] **Step 4: verify** — mypy/black/pytest for changed Python; controller does in-browser check (toggle reflects, editing a `.py` file triggers an incremental run that streams).
- [ ] **Step 5: commit** `git add -A && git commit -m "feat(web): --watch CLI flag + SPA watch toggle"`

---

## Self-Review Notes
- Infinite-loop guard: `_should_watch` excludes `output_dir` (where `analysis.json` is written) and `.git` — the critical correctness property; covered by `test_ignores_output_dir`/`test_ignores_git_dir`.
- Reuse: triggers the existing `AnalysisRunner.start` + SSE/diagram-delta path; no engine changes.
- Type consistency: `on_change: Callable[[], None]` (W1) matches the closure passed in W2. `create_app(..., watch=False)` (W2) consumed by serve (W3).
