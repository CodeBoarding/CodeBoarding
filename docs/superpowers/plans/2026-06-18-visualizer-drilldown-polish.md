# Visualizer Upgrade: Drill-down, Detail Sidebar, Polish, Open-Source — Plan

> Increment on `feat/standalone-web-visualizer`. Brings the standalone SPA closer to the VS Code extension: click-to-drill-into-components with a breadcrumb, a node-detail sidebar with Key Code Entities, a polished dark theme + toolbar, and click-to-open-source.

**Goal:** Make `codeboarding serve` an interactive explorer: drill into components, inspect a node's description + key code entities, open those in the editor, on a nicer-looking graph.

**Architecture:** Backend enriches each Cytoscape node with detail (description, key entities w/ editor URLs, expandable flag, componentId) and serves per-component sub-graphs. Frontend keeps one persistent Cytoscape instance, navigates a breadcrumb stack across overview ↔ sub-graphs, shows a detail sidebar on selection, and restyles the graph.

**Tech Stack:** existing FastAPI + Cytoscape/Dagre; no new deps.

## Global Constraints
- Black 120; type hints; builtin generics; no function-level imports; no `if TYPE_CHECKING`; terse docstrings.
- Do NOT modify the shared `output_generators/` renderer (used by static HTML/markdown). Enrichment lives in `codeboarding_web/diagram.py` only.
- `uv run pytest --ignore=tests/integration`, `uv run mypy .`, `uv run black . --check` clean.
- Backward compatible: live-streaming (diagram_delta/run flow) keeps working.

## Data facts (grounding)
- `Component`: `component_id`, `name`, `description`, `key_entities: list[SourceCodeReference]`.
- `SourceCodeReference`: `qualified_name`, `reference_file: str|None`, `reference_start_line: int|None`, `reference_end_line: int|None`.
- `parse_unified_analysis(data) -> (root_analysis, sub_analyses)`; `sub_analyses` keyed by `component_id`.
- Overview expanded set = `set(sub_analyses.keys())`; a node is *expandable* iff its `component_id ∈ sub_analyses`.
- Node id in cytoscape = `sanitize(comp.name)` (from `output_generators.html.generate_cytoscape_data`).

---

## Task V1: Backend — enrich node detail + per-component sub-graph loader

**Files:** Modify `codeboarding_web/diagram.py`, `codeboarding_web/runner.py` (call-site), `codeboarding_web/app.py` (call-sites + new route); Test `tests/web/test_diagram.py`, extend `tests/web/test_app.py`.

**Interfaces (Produces):**
- `load_cytoscape(output_dir: Path, project: str, repo_path: Path) -> dict | None` — overview, ENRICHED.
- `load_cytoscape_component(output_dir: Path, project: str, repo_path: Path, component_id: str) -> dict | None` — sub-graph for `component_id`; None if absent/unreadable.
- Enrichment: each node's `data` gains:
  - `componentId: str`
  - `expandable: bool` (component_id in the analysis's sub_analyses keys)
  - `keyEntities: list[dict]` — `{ "qname": str, "file": str|None, "startLine": int|None, "endLine": int|None, "openUrl": str|None }`
    - `openUrl` = `f"vscode://file/{abs}:{startLine}"` where `abs` = `reference_file` if absolute else `str((repo_path / reference_file).resolve())`; `None` when `reference_file`/`startLine` missing.
  - (`description` is already present from `generate_cytoscape_data`.)
- Enrichment is applied by post-processing `generate_cytoscape_data(...)` output: build `by_id = {sanitize(c.name): c for c in analysis.components}` and attach detail to matching nodes (node id == `sanitize(name)`, the same key the renderer uses — exact, not fuzzy).

- [ ] **Step 1: failing tests** extend `tests/web/test_diagram.py` (reuse the real-save fixture pattern already in that file; add a component with a `key_entities` reference and a sub-analysis so expandable=True and keyEntities populated):
```python
def test_overview_nodes_enriched(tmp_path):
    _write_analysis(tmp_path)   # existing helper; ensure the component has key_entities + a sub-analysis
    result = load_cytoscape(tmp_path, "demo", tmp_path)
    node = next(e for e in result["elements"] if "source" not in e["data"])
    assert "componentId" in node["data"]
    assert "expandable" in node["data"]
    assert "keyEntities" in node["data"]


def test_component_subgraph_loads(tmp_path):
    _write_analysis(tmp_path)
    # component_id of the expandable component from the fixture:
    sub = load_cytoscape_component(tmp_path, "demo", tmp_path, "<expandable_component_id>")
    assert sub is not None and "elements" in sub


def test_component_subgraph_missing_returns_none(tmp_path):
    _write_analysis(tmp_path)
    assert load_cytoscape_component(tmp_path, "demo", tmp_path, "nonexistent") is None
```
> Implementer: adjust `_write_analysis` to include a `key_entities` entry (a `SourceCodeReference` with `qualified_name`, `reference_file`, `reference_start_line`, `reference_end_line`) and at least one sub-analysis keyed by a component_id, built via the real models + `save_analysis` so `parse_unified_analysis` reads it. Use that component_id in `test_component_subgraph_loads`.

- [ ] **Step 2: run → fail.** `uv run pytest tests/web/test_diagram.py -v`.

- [ ] **Step 3: implement** the enrichment + loaders in `diagram.py`. Add `from utils import sanitize` (the same sanitizer the renderer uses) and `from pathlib import Path` (present). Build `_enrich(elements, analysis, sub_analyses, repo_path)` that mutates node data, and an `_open_url(repo_path, ref)` helper. `load_cytoscape` and `load_cytoscape_component` both parse via `parse_unified_analysis`, call `generate_cytoscape_data`, then `_enrich`. Keep the existing None-on-error guards.

- [ ] **Step 4: thread `repo_path`** through call-sites:
  - `runner.py` `_progress_callback`: `load_cytoscape(self.output_dir, self.project_name, self.repo_path)`.
  - `app.py` `/api/diagram.json`: `load_cytoscape(output_dir, project_name, repo_path)`.
  - `app.py` add route `GET /api/diagram/{component_id}` → `load_cytoscape_component(output_dir, project_name, repo_path, component_id)`; 404 if None.

- [ ] **Step 5: app test** extend `tests/web/test_app.py`: with a baseline analysis present, `GET /api/diagram/<id>` returns 200 + elements; an unknown id returns 404. (Reuse a fixture writing a real analysis to the client's output_dir; or assert 404 path with no baseline.)

- [ ] **Step 6: run → pass.** `uv run pytest tests/web/ -q`; `uv run mypy codeboarding_web/ tests/web/`; `uv run black codeboarding_web/ tests/web/ --check`.

- [ ] **Step 7: commit** `git add -A && git commit -m "feat(web): enrich node detail + per-component sub-graph endpoint"`

---

## Task V2: Frontend — drill-down, breadcrumb, detail sidebar, polish, open-source

**Files:** Modify `codeboarding_web/static/index.html`, `app.css`, `app.js`. Verified in-browser by the controller (no unit test).

**Layout (approximate the extension):**
- Header: project + phase chip + scope select + Run + Watch (existing) and a **breadcrumb** (`Overview › Static Analysis Engine › …`).
- Left **detail sidebar** (`#detail`): selected node's name, description, and **Key Code Entities** (each `keyEntities` row; if `openUrl` present, render as `<a href="openUrl">qname:line</a>`, else plain text). Empty-state when nothing selected.
- Center graph (`#cy`).
- Progress **log** kept (compact) — e.g. a collapsible panel or the lower part of the sidebar; must stay visible during runs.
- A small **toolbar** over the graph: zoom in / zoom out / fit / relayout.

**Cytoscape styling (polish):**
- Nodes: `shape: 'round-rectangle'`, padding, readable label, dark fill (`#262626`) with accent border (`#FFC107` selected / `#3a3a3a` default), white text. Expandable nodes get a distinct affordance (thicker/gold left border or a `⊕`/`›` suffix on the label).
- Edges: grey (`#555`), `target-arrow-shape: 'triangle'`, `curve-style: 'bezier'`, small label.
- `selected` node: gold border highlight.

**Interactions:**
- `cy.on('tap','node', …)` → select node, populate `#detail` from `node.data()` (description + keyEntities).
- Drill in: `cy.on('dbltap','node[expandable]', …)` (or a dedicated expand affordance) → push `{id: componentId, label: name}` onto a breadcrumb stack, `fetch('/api/diagram/'+componentId)`, render the sub-graph (this is a NEW graph → run layout + fit), clear selection.
- Breadcrumb click → pop to that level: Overview = `/api/diagram.json`; deeper = `/api/diagram/<id>`. Render with layout+fit.
- Toolbar: `cy.zoom`, `cy.fit`, re-run `dagre` layout.
- Open-source: Key Code Entity links use the `openUrl` (`vscode://file/...`) so the OS opens VS Code at the file/line. No JS needed beyond rendering the anchor.

**Live updates with drill-down:**
- Keep the existing persistent-instance, viewport-preserving `applyElements` for the OVERVIEW level only.
- On `diagram_delta`: if the breadcrumb is at Overview, `applyElements` in place (preserve pan/zoom) as today; if drilled in, ignore the delta (don't clobber the sub-graph).
- On `run_end`: re-fetch the CURRENT breadcrumb level and render (so a drilled-in view refreshes after a run). At Overview, this is the existing reload.
- Keep `watch_triggered` / `run_notice` / step listeners as-is.

**Steps:**
- [ ] **Step 1** rewrite `index.html` with the new layout (breadcrumb, `#detail` sidebar, `#cy`, toolbar, compact log). Keep the CDN script tags.
- [ ] **Step 2** restyle `app.css` (dark palette already: bg `#1E1E1E`, text `#E6E6E6`, accents info `#2196F3` / warn `#FFC107` / ok `#4CAF50` / err `#F44336`); add sidebar, breadcrumb, toolbar, and round-node-friendly styles.
- [ ] **Step 3** rewrite `app.js`: persistent `cy`; `STYLE` per above; breadcrumb stack + `navigateTo(level)`; `renderGraph(elements, {fit})`; `applyElements` (in-place, overview only); `selectNode`/`renderDetail`; tap/dbltap handlers; toolbar handlers; SSE listeners (reuse existing event names; adjust `diagram_delta`/`run_end` per "Live updates" above).
- [ ] **Step 4** `node --check codeboarding_web/static/app.js`; controller verifies in-browser: overview renders polished; clicking a node fills the sidebar; double-clicking an expandable node drills in + breadcrumb; breadcrumb navigates back; Key Code Entity links are `vscode://` anchors; toolbar zoom/fit/relayout work; a run still streams at the overview level.
- [ ] **Step 5** commit `git add codeboarding_web/static/ && git commit -m "feat(web): drill-down, detail sidebar, polish, open-source links"`

---

## Self-Review Notes
- No change to `output_generators/` (shared renderer) — enrichment is web-only (`diagram.py`).
- Node-id match for enrichment uses the SAME `sanitize(name)` key the renderer emits — exact, not fuzzy.
- Drill-down reuses existing per-component sub-analyses; `load_cytoscape_component` mirrors the renderer's sub-expansion (`{c.component_id for c in sub.components if c.component_id in sub_analyses}`).
- Live-delta only applies at overview to avoid clobbering a drilled-in view; run_end re-fetches current level.
- `repo_path` threaded to `diagram.py` only to build `vscode://` open URLs; safe (no new deps).
