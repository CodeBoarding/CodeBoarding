# Visualizer Parity: In-place Expand, Badges, Tabs, See-Diff — Plan

> Increment on `feat/standalone-web-visualizer`. Closes most of the gap to the VS Code extension: expand a node IN PLACE into a nested compound box (recursive ⊕/collapse), per-node warning/modification badges, sidebar tabs (Overview/Warnings/Modifications), Source Files, Copy Context, and a per-component git "see diff".

**Goal:** Make `codeboarding serve` explore like the extension — in-place nested expansion plus a rich per-component sidebar with diffs.

**Tech Stack:** existing FastAPI + Cytoscape (compound nodes) + cytoscape-dagre; `repo_utils` git diff; `health_report.json`. No new deps.

## Global Constraints
- Black 120; type hints; builtin generics; no function-level imports; no `if TYPE_CHECKING`; terse docstrings.
- Do NOT modify shared `output_generators/` renderer — enrichment stays in `codeboarding_web/diagram.py`.
- `uv run pytest --ignore=tests/integration`, `uv run mypy .`, `uv run black . --check` clean.
- Live-streaming + watch + existing drill must keep working (in-place expand REPLACES the full-view drill-down; breadcrumb stays for top-level scope changes if still useful, else removed).

## Data facts
- `health_report.json` → `file_summaries: list[{file_path(repo-rel), warning_findings, total_findings, composite_risk_score}]`.
- A component's files ≈ `{normalize(ref.reference_file) for ref in comp.key_entities if ref.reference_file}` (repo-relative).
- Modifications = component files ∩ git-changed files (`detect_changes(repo, "HEAD", "")` = working tree + untracked).
- `run_raw_diff(repo, base, target, ...)` raw diff; for a readable patch use a plain `git diff <base> [<target>] -- <files>` (patch, not --raw).
- Sub-graphs already served by `GET /api/diagram/{component_id}`.

---

## Task E1: Backend — per-component warnings/modifications/sourceFiles + diff endpoint

**Files:** Modify `codeboarding_web/diagram.py` (enrichment), `codeboarding_web/app.py` (diff route), add `codeboarding_web/component_data.py` (health + git helpers). Test `tests/web/test_component_data.py`, extend `tests/web/test_diagram.py`, `tests/web/test_app.py`.

**Interfaces (Produces):**
- `component_data.py`:
  - `component_files(comp) -> set[str]` — repo-relative files from `comp.key_entities`.
  - `load_warning_counts(output_dir: Path) -> dict[str, int]` — `{repo_rel_file: warning_findings}` from `health_report.json` (empty dict if absent).
  - `changed_files(repo_path: Path) -> set[str]` — repo-relative changed files via `detect_changes(repo_path, "HEAD", "")` (empty set on error).
  - `component_diff(repo_path: Path, files: list[str]) -> str` — `git diff HEAD -- <files>` patch text (empty string if none/error).
- `diagram.py` `_enrich(...)` ALSO sets per node: `warnings: int` (sum warning_findings over component files), `modifications: int` (count of component files in changed set), `sourceFiles: list[str]`. (Enrichment now takes `warning_counts` + `changed` precomputed once per call to avoid re-reading per node.)
- `app.py`: `GET /api/component/{component_id}/diff` → `{component_id, files: [...], diff: "<patch>"}` (404 if component absent).

- [ ] **Step 1: failing tests** `tests/web/test_component_data.py`:
```python
from pathlib import Path
from codeboarding_web.component_data import load_warning_counts, changed_files


def test_load_warning_counts_absent(tmp_path):
    assert load_warning_counts(tmp_path) == {}


def test_load_warning_counts_reads_health(tmp_path):
    import json
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "health_report.json").write_text(json.dumps(
        {"file_summaries": [{"file_path": "a.py", "warning_findings": 3}]}))
    assert load_warning_counts(tmp_path) == {"a.py": 3}


def test_changed_files_non_git(tmp_path):
    # non-git dir → empty set, no crash
    assert changed_files(tmp_path) == set()
```
Extend `tests/web/test_diagram.py`: assert an overview node has `warnings`, `modifications`, `sourceFiles` keys. Extend `tests/web/test_app.py`: `GET /api/component/<id>/diff` → 200 with `diff`/`files` keys for a real id (write baseline), 404 for unknown id.

- [ ] **Step 2: run → fail.**

- [ ] **Step 3: implement** `component_data.py` (helpers), wire `_enrich` to set `warnings`/`modifications`/`sourceFiles` (compute `warning_counts = load_warning_counts(output_dir)` and `changed = changed_files(repo_path)` ONCE in `load_cytoscape`/`load_cytoscape_component` and pass into `_enrich`). Add the diff route to `app.py`.
   - Path normalization: make `component_files` and `changed_files`/`warning_counts` agree on repo-relative form (strip a leading repo_path prefix / use `os.path.relpath` against repo_path for absolute `reference_file`).

- [ ] **Step 4: run → pass.** `uv run pytest tests/web/ -q`; mypy; black.
- [ ] **Step 5: commit** `git add -A && git commit -m "feat(web): per-component warnings/modifications/sourceFiles + diff endpoint"`

---

## Task E2: Frontend — in-place compound expand / collapse (headline)

**Files:** Modify `codeboarding_web/static/{index.html,app.css,app.js}`. In-browser verified.

**Model change:** replace the full-view drill-down with IN-PLACE expansion using Cytoscape compound nodes.
- Each node with `expandable:true` shows a ⊕ expand affordance (a Cytoscape node overlay button, or a tap target). On expand:
  - `fetch('/api/diagram/'+componentId)` → for the returned sub-graph, add its nodes as CHILDREN of the expanded node (`data.parent = <componentId-sanitized-id>`), add its internal edges, and mark the parent as a compound (`data.collapsed=false`). Re-run layout (`dagre` supports compound; if layout looks poor, fall back to `fcose` IF available, else keep dagre with `nodeDimensionsIncludeLabels`). Animate fit to the expanded parent.
  - Track expanded set so re-expansion is idempotent.
- Collapse: a ⊖ affordance on an expanded parent removes its descendant children/edges (recursively) and re-lays out.
- Recursive: a child that is itself `expandable` shows its own ⊕.
- Keep a top "Overview" reset (collapse all) control.
- Selection/detail sidebar still works on any node (parent or child).

**Live updates:** `diagram_delta` at overview still applies in place to the TOP-LEVEL nodes only; expanded children are left intact (or collapse-all before applying a full delta — simplest: on `diagram_delta`, if anything is expanded, skip; refresh on `run_end`). Keep watch/run flow working.

**Steps:**
- [ ] **Step 1** `app.js`: compound `STYLE` (parent nodes: padding, header label at top, translucent fill, gold border; child nodes as before). Expand/collapse handlers; `expandNode(id)`/`collapseNode(id)`; idempotent expanded-set; layout after each.
- [ ] **Step 2** `index.html`/`app.css`: any controls (Collapse all), compound node styling.
- [ ] **Step 3** `node --check`; controller verifies in-browser: ⊕ expands a node into a nested box keeping siblings; nested ⊕ works; ⊖ collapses; selection/detail still works; a run still streams at overview.
- [ ] **Step 4** commit.

> Risk: compound layout quality with dagre. If dagre yields overlap, try `cy.layout({name:'dagre', rankDir:'TB', nestingFactor, ...})` tuned params; document the chosen params. Keep one persistent `cy`.

---

## Task E3: Frontend — badges, sidebar tabs, Source Files, Copy Context, See-diff

**Files:** Modify `codeboarding_web/static/{index.html,app.css,app.js}`. In-browser verified.

- **Node badges:** render `warnings`/`modifications` counts on nodes (Cytoscape node label HTML isn't supported; use a small badge via `text` in the label, or a second stacked label / `pie`/`overlay`). Simplest: append `  ⚠{warnings} ✎{modifications}` to the node label when >0, or use `cytoscape-node-html-label` (avoid new dep) → fallback to label suffix.
- **Sidebar tabs:** Overview / Warnings / Modifications. Overview = description + Key Code Entities (existing) + Source Files (`sourceFiles`). Warnings tab = the component's warning files/counts. Modifications tab = `GET /api/component/{id}/diff` → list Modified Files + a `<pre>` diff with +/- line coloring; each file also a `vscode://` link.
- **Copy Context:** a button that copies the component's name + description + key entities (+ source files) to the clipboard as markdown (for pasting into a coding agent).
- **See-diff:** within Modifications tab, render the patch text returned by the diff endpoint, colorized (lines starting `+`/`-`).

**Steps:**
- [ ] **Step 1** `index.html`/`app.css`: tab bar in `#detail`, badge styles, diff `<pre>` styles (+green/-red).
- [ ] **Step 2** `app.js`: tab switching; render Source Files; Warnings tab; Modifications tab fetches the diff endpoint and renders colorized patch + vscode links; Copy Context via `navigator.clipboard.writeText`; node label badge suffix.
- [ ] **Step 3** `node --check`; controller verifies in-browser: badges show on nodes with warnings/mods; tabs switch; Source Files list; Copy Context copies; Modifications tab shows the git diff for a changed component.
- [ ] **Step 4** commit.

---

## Self-Review Notes
- Backend reads (`health_report.json`, git diff) are best-effort: missing/non-git → empty, never crash a diagram load.
- Enrichment computes warning_counts + changed set ONCE per diagram request (not per node).
- In-place expand uses Cytoscape compound nodes (native) + existing sub-graph endpoint — no new backend for E2.
- E3 consumes E1's node data + diff endpoint.
- All frontend changes keep ONE persistent `cy` and the overview live-stream path.
