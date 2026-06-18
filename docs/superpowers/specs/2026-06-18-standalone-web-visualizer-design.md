# Standalone Web Visualizer (`codeboarding serve`) — Design

**Date:** 2026-06-18
**Status:** Approved (pending spec review)
**Branch:** `feat/standalone-web-visualizer` (fork `paulrobello/CodeBoarding`)

## 1. Goal

Decouple the "real-time interactive" experience of the CodeBoarding VS Code
extension into a standalone, editor-independent local web app. A new
`codeboarding serve` command starts a local FastAPI server, opens the browser,
renders any existing analysis immediately, and lets the user trigger a **full**
or **incremental** run whose progress and **diagram updates stream live**
(mid-run, per component) into the page — preserving the user's pan/zoom/focus
throughout.

The extension source lives in a separate repo and is not available; this work
reconstructs the interactive behavior on top of this repo's existing engine and
diagram renderer.

## 2. Why this fits the existing codebase

- **Deps already present:** `fastapi>=0.115` and `uvicorn>=0.23` are already in
  `pyproject.toml`. SSE is hand-rolled on FastAPI `StreamingResponse` — **no new
  dependency**.
- **Progress already emitted:** `@trace`-decorated agents and
  `monitor_execution().step()` write structured JSONL events (`run_start`,
  `step_start`, `step_end`, `step_error`, `phase_change`, `run_end`) to the
  `traces` logger. The web layer taps that logger — no new pipeline
  instrumentation for step/token progress.
- **Diagram data already incremental:** `DiagramGenerator._generate_subcomponents`
  (`diagram_analysis/diagram_generator.py:428`) already re-saves the full
  `analysis.json` after *every* component completes ("Saving intermediate
  analysis"). The diagram data is therefore produced incrementally during the
  run for both full and incremental scopes. We surface those completion points
  to the browser.
- **Orchestration already reusable:** `run_analysis_pipeline()` +
  `run_full` / `run_incremental` / `run_incremental_workflow` +
  `render_docs` / `generate_cytoscape_data`. The web runner is a thin driver
  around the same `scope()` shape used in `full_analysis.py:164`. The
  orchestration docstrings already anticipate an external "desktop wrapper".
- **Renderer already framework-agnostic:** `output_generators/html.py`
  `generate_cytoscape_data()` builds Cytoscape elements JSON, reused directly.

## 3. Architecture

New self-contained package `codeboarding_web/`:

```
codeboarding_web/
  __init__.py
  app.py          # FastAPI app factory + routes
  runner.py       # background analysis task; drives run_analysis_pipeline scope
  events.py       # TraceEventBroker (logging.Handler -> asyncio.Queue) + SSE encoder
  state.py        # RunState (idle/running/done/error), run_id, repo/output paths, busy guard
  diagram.py      # analysis -> Cytoscape JSON adapter (wraps generate_cytoscape_data)
  static/
    index.html    # SPA shell: persistent Cytoscape container + progress panel + run controls
    app.js        # EventSource client; live progress; in-place graph delta apply
    app.css
```

CLI seam (mirrors existing subcommands in `main.py`):

- Add `"serve"` to `_SUBCOMMANDS`.
- New `codeboarding_cli/commands/serve_analysis.py` with `add_arguments(...)` and
  `run_from_args(...)`; the latter launches uvicorn with the configured app.
- Dispatch in `main.py`: `if args.command == "serve": serve_analysis.run_from_args(args, parser)`.
- Args: `--local <path>` (default cwd), `--output-dir`, `--project-name`,
  `--host` (default `127.0.0.1`), `--port` (default `8050`), `--no-open`.

Default port **8050** (reserved in `~/.claude/used_ports.md`). Bind to
`127.0.0.1` only (local single-user tool; no auth in v1).

## 4. Routes

| Method | Path | Purpose |
|---|---|---|
| GET  | `/`              | SPA shell (static `index.html`) |
| GET  | `/api/status`    | `RunState` + whether an `analysis.json` baseline exists |
| GET  | `/api/diagram.json` | Current Cytoscape elements JSON (404 until one exists) |
| POST | `/api/run`       | Body `{scope:"full"\|"incremental", base_ref?, target_ref?}`; starts run; **409** if busy |
| GET  | `/api/events`    | SSE stream of progress + diagram-delta events for the active run |

## 5. Progress + diagram streaming

### 5.1 Step/token progress (existing `traces` logger)

`events.TraceEventBroker` is a `logging.Handler` attached to the `traces` logger
for the run's duration. Each emitted record is already JSON; the broker parses it
and hands it to the event loop via `loop.call_soon_threadsafe(queue.put_nowait, ...)`
(the pipeline runs on a worker thread; this is the thread→loop handoff). The
`/api/events` SSE generator drains the queue and emits framed events
(`step_start`, `step_end`, `phase_change`, `run_end`, `run_error`, plus a
periodic `token_usage` snapshot from `RunStats`). The handler is detached in a
`finally`, mirroring `monitor_execution`'s teardown.

### 5.2 Mid-run diagram deltas (new optional callback)

Add an **optional** `progress_callback` to `DiagramGenerator` (threaded through
`build_generator` in `codeboarding_workflows/analysis.py`), default `None`.
It is invoked right after each intermediate `save_analysis` in
`_generate_subcomponents` (`diagram_generator.py:~434`) and after the
incremental redetail path (`:~645`), receiving the current
`(analysis, sub_analyses)`. CLI and GitHub Action pass nothing → **no behavior
change**.

The web `runner` supplies a callback that converts the snapshot to Cytoscape
JSON (`diagram.py`) and enqueues a `diagram_delta` event. Each delta carries the
**full current** elements set (cheap locally); the client computes the visual
delta against the live graph.

### 5.3 Client-side in-place apply (viewport preservation)

`app.js` owns **one long-lived `cytoscape` instance** for the session. On each
`diagram_delta`:

1. Capture `cy.pan()` and `cy.zoom()`.
2. Diff incoming elements vs. current: `cy.add()` new, `cy.remove()` gone,
   update changed node/edge data.
3. Run layout **only on newly added nodes** with existing node positions locked
   (no global Dagre re-layout, no `cy.fit()`), so the graph does not jump.
4. Restore captured pan/zoom; briefly **highlight** changed/added nodes (pulse)
   so the user sees *what* changed without losing *where* they are.

This is why the iframe-reload approach was rejected: reloading a generated HTML
document re-initializes Cytoscape, re-runs global layout, and resets the
viewport on every update.

## 6. Run lifecycle & concurrency

- **Startup:** if `analysis.json` exists for the target repo, the client loads
  `/api/diagram.json` and renders immediately. A "Run / Re-run" control offers
  `full` and `incremental` scopes.
- **Single active run (v1):** `state.py` guards a busy flag; `POST /api/run`
  returns 409 while a run is in flight.
- **Incremental:** UI exposes `base_ref` (default `HEAD~1`) and `target_ref`
  (default `HEAD`). Server calls the existing `run_incremental`; a
  `BaselineUnavailableError` (or incremental cache-missing error) is surfaced as
  a "run a full analysis first" prompt — identical semantics to the CLI.
- **Teardown:** broker handler + progress callback are removed in `finally`
  whether the run succeeds or raises; final `run_end` / `run_error` event closes
  the SSE stream.

## 7. Testing (≥80% coverage per AGENTS.md)

Unit tests (no real LLM calls — `runner` mocked):

- `TraceEventBroker`: record → event mapping; thread-safe enqueue via a stub loop.
- `state`: idle→running→done/error transitions; busy guard rejects concurrent run.
- Routes via FastAPI `TestClient`: `/api/status`, `/api/diagram.json` (present /
  404), `/api/run` happy path + 409 when busy, incremental
  `BaselineUnavailableError` → prompt payload.
- SSE generator yields expected framed events from a seeded queue.
- `diagram.py`: snapshot → Cytoscape JSON adapter shape.

Commands: `uv run pytest --ignore=tests/integration`, `uv run mypy .`,
`uv run black . --check`. Client `app.js` delta logic kept small and pure where
possible; covered by a lightweight DOM-free unit where practical, otherwise
manual verification in-browser.

## 8. Constraints honored

From `AGENTS.md`, `CONTRIBUTING.md`, `REVIEW.md`, and global prefs:

- Python ≥3.12; Black line length 120; type hints on all new code.
- Builtin generics (`dict`/`list`/`set`); no `typing.Dict` etc.
- No function-level imports; no `if TYPE_CHECKING` blocks.
- Terse docstrings (one-line; optional `Why:`); no per-submodule docstring
  copy-paste.
- `snake_case` vars, `PascalCase` classes.
- Phased execution, ≤5 files per phase. The only core-file touch is the optional
  `progress_callback` seam (§5.2); everything else is additive under
  `codeboarding_web/` and a new CLI command.
- Branch `feat/standalone-web-visualizer`; PR body in WHAT/WHY/HOW form.

## 9. Out of scope (v1, YAGNI)

- Watch-on-save automatic re-analysis (manual trigger only).
- WebSocket bidirectional control (SSE is one-directional, sufficient;
  SSE→WS is the only transport change if mid-run cancel is wanted later).
- Click-to-drill-down / expand sub-component pages (v1 shows the overview graph;
  the multi-page `./{node}.html` drill-down links are not wired into the SPA).
- Click-to-open source in an editor.
- Multi-run concurrency, auth, multi-tenant, hosted deployment, desktop
  packaging.

## 10. Phasing

1. **Phase 1 — engine seam:** optional `progress_callback` in `DiagramGenerator`
   + `build_generator`; unit test it leaves CLI/Action behavior unchanged.
2. **Phase 2 — web core:** `state.py`, `events.py`, `diagram.py`, `runner.py`
   + unit tests.
3. **Phase 3 — app + CLI:** `app.py` routes, `serve_analysis` command, `main.py`
   wiring + route tests.
4. **Phase 4 — frontend:** `static/` SPA shell, live progress, in-place delta
   apply; in-browser verification.
5. **Phase 5 — docs + polish:** README section, `make checkall` green, PR.
