# Local Web Visualizer (`codeboarding serve`)

`codeboarding serve` runs a local web app that renders your repository's
architecture as an interactive diagram. It serves the same analysis the CLI
produces, but lets you explore it in a browser — drill into components, inspect
their code entities, see what changed, and re-run analysis with live progress —
without an editor extension.

```bash
codeboarding serve --local /path/to/repo
```

This starts a server at `http://127.0.0.1:8050/` and opens your browser. If an
analysis already exists in `<repo>/.codeboarding/`, it renders immediately;
otherwise use **Run analysis** to generate one.

> Requires an LLM provider to be configured (same as the CLI) — `serve` runs the
> normal bootstrap at startup, so a provider key must be set in
> `~/.codeboarding/config.toml` or the environment before running an analysis.

## Command-line flags

| Flag | Default | Description |
|---|---|---|
| `--local PATH` | (required) | Repository to serve |
| `--depth-level INT` | `2` | Analysis depth. **≥2 generates nested sub-components you can expand in the diagram; `1` is a flat, top-level-only view.** |
| `--host HOST` | `127.0.0.1` | Bind address |
| `--port PORT` | `8050` | Bind port |
| `--no-open` | off | Don't open a browser tab on start |
| `--watch` / `--no-watch` | on | Auto re-analyze on source-file changes (see [Watch-on-save](#watch-on-save)) |
| `--output-dir PATH` | `<repo>/.codeboarding` | Where the analysis is read from / written to |
| `--project-name NAME` | repo dir name | Display name |

## The interface

**Header**
- **Project name** and a **phase chip** (`idle` / `running` / `done` / `error`).
- **`depth N`** indicator — turns gold and warns when depth ≥ 3 (deep runs are slow and costly).
- **Scope** select (`Full` / `Incremental`) and **Run analysis** to (re)generate.
- **Stop ◼** — appears while a run is in progress (see [Stopping a run](#stopping-a-run)).
- **Watch** checkbox — toggles watch-on-save at runtime.
- **Overview** + **Collapse all** — reset the diagram to the top level.

**Graph toolbar** (above the diagram): zoom in, zoom out, fit-to-view, re-run layout.

**Progress log** (right panel, collapsible): streams analysis steps and notices.

## Exploring the diagram

### Expanding components
Nodes with a **gold border and a `⊕`** in their label are *expandable* — they
have a sub-diagram. **Single-click an expandable node to expand it in place**
into a nested box (its `⊕` becomes `⊖`); the surrounding nodes stay visible.
Expansion is recursive — a nested child that is itself expandable shows its own
`⊕`. Click an expanded node again to collapse it, or use **Collapse all**.

> If **no** nodes are gold/`⊕`, the analysis has no sub-components — it was
> generated at `--depth-level 1`. Re-run a **Full** analysis at depth ≥ 2.

You can also select a node and use the **Expand / Collapse** button in the
sidebar. Leaf nodes (grey border, no `⊕`) only show details when clicked.

### Node badges
Expandable and leaf nodes show small badges when relevant:
- **`⚠N`** — number of static-analysis warnings in the component's files.
- **`✎N`** — number of the component's files changed in your working tree.

### The detail sidebar
Selecting a node opens a sidebar with three tabs:

- **Overview** — the component's description, its **Key Code Entities**
  (classes/methods, each a clickable `vscode://` link that opens the file at the
  line in VS Code), and its **Source Files**.
- **Warnings** — per-file static-analysis warning counts for the component.
- **Modifications** — the component's **modified files** and a color-coded
  **git diff** of the changes (uncommitted working-tree changes vs `HEAD`).

A **Copy Context** button copies the component's name, description, key
entities, and source files as Markdown — handy for pasting into a coding agent.

## Running analysis with live progress

Click **Run analysis** with **Full** or **Incremental** scope. While it runs:
- the **progress log** streams each step and phase,
- the **diagram updates live** as each component is analyzed, and
- at the overview level your **pan/zoom and selection are preserved** across
  updates (drilled-in views refresh when the run completes).

**Incremental** diffs your working tree against `HEAD` and re-analyzes only the
changed clusters. If the warm-start cache isn't seeded yet (e.g. the baseline
was produced elsewhere), incremental **automatically falls back to a full run**
to seed it — you'll see a "running a full analysis to seed it" notice, and
subsequent incremental runs are fast.

### Watch-on-save
With watch enabled (default), saving a source file in the repo automatically
triggers an **incremental** re-analysis and streams the updated diagram. The
log shows `change detected → re-analyzing`. Toggle it from the header
**Watch** checkbox or start with `--no-watch`. The watcher ignores the
`.codeboarding/` output directory and `.git/`, so analysis writes never
retrigger it.

### Stopping a run
A long run (especially at high `--depth-level`) can be stopped with the
**Stop ◼** button. Cancellation is **cooperative**: it stops launching new
component work, lets in-flight calls finish, and keeps the **partial** result —
so it halts further token spend without killing the server. Cancellation takes
effect within roughly one in-flight LLM call.

## Choosing a depth

Depth controls how many nested levels the analysis produces — and how much it
costs:

- **`1`** — flat, top-level components only. Nothing to expand. Fast/cheap.
- **`2`** (default) — one level of expandable sub-components. The sweet spot for
  the visualizer.
- **`3`+** — each sub-component is broken down again. The number of LLM calls
  multiplies per level, so depth 3–4 can take many minutes and a lot of tokens.
  The header shows a gold warning at ≥ 3; use **Stop** if a run runs long.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Double/single-click does nothing; no `⊕` on any node | The analysis is flat (depth 1). Re-run **Full** at `--depth-level 2`. |
| UI looks stale after updating CodeBoarding | The browser cached the old page. **Hard-refresh** once (Cmd/Ctrl+Shift+R); responses are sent `no-cache` so it won't recur. |
| A run is taking a very long time | Likely a high `--depth-level`. Use **Stop**, then re-run at depth 2. |
| "Incremental analysis cannot proceed… run a full analysis first" | No baseline or unseeded warm cache. The web app self-heals (falls back to full); or run **Full** once. |
| Source / entity links don't open | They use `vscode://file/...`; VS Code (or a `vscode://` handler) must be installed. |

## HTTP endpoints

The SPA is driven by a small JSON/SSE API (useful for scripting or debugging):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Phase, project, depth, baseline + watch flags |
| GET | `/api/diagram.json` | Overview Cytoscape elements |
| GET | `/api/diagram/{component_id}` | A component's sub-graph |
| GET | `/api/component/{component_id}/diff` | Changed files + git diff for a component |
| POST | `/api/run` | Start a run (`{scope, base_ref?, target_ref?}`) |
| POST | `/api/watch` | Toggle watch-on-save (`{enabled}`) |
| POST | `/api/cancel` | Stop the in-flight run |
| GET | `/api/events` | Server-Sent Events: progress, diagram deltas, run lifecycle |
