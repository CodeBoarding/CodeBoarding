# Incremental Semantic Tracer Plan

## Goal

Build the semantic incremental analysis flow on top of the current `main` / `iterative-llm-based` direction, using the rebased `new-iterative` work as source material, while reshaping it into a flatter architecture that matches the rest of the repository.

The target behavior is:

- Incremental analysis compares git commits using the existing commit-based model.
- Structural changes are computed deterministically first.
- Tree-sitter removes cosmetic-only edits before any semantic tracing LLM step.
- The semantic tracer walks CFG neighbors to determine upstream and downstream impact.
- Patching is scoped to the components/subtrees justified by the trace.
- Incremental analysis never returns "full analyze", "reexpand", or any equivalent escape hatch once a baseline exists.

## Guiding Decisions

- Do not merge the `new-iterative` architecture as-is.
- Keep incremental as an alternate execution mode of `DiagramGenerator`, not a separate product subsystem.
- Keep structural mutation deterministic and semantic updates LLM-scoped.
- Express uncertainty as broader patch scope, never as a fallback to full analysis.
- Flatten the module layout so it matches the existing `diagram_analysis` style.

## Target File Layout

```text
diagram_analysis/
  analysis_json.py
  diagram_generator.py
  io_utils.py
  incremental_models.py
  incremental_updater.py
  incremental_tracer.py
  analysis_patcher.py

static_analyzer/
  semantic_diff.py
  graph_query.py

codeboarding_workflows/
  incremental.py
```

At the end of the work:

- `diagram_analysis/incremental/` should no longer exist.
- `diagram_analysis/incremental_types.py` should be absorbed into the new flat files.
- `agents/analysis_patcher.py` should be removed in favor of `diagram_analysis/analysis_patcher.py`.
- The broader `new-iterative` workflow refactor should not be pulled in unless a specific piece is required for the incremental flow.

## Required Restructuring

### 1. Move workflow concerns to `codeboarding_workflows/incremental.py`

Move `diagram_analysis/incremental/pipeline.py` into `codeboarding_workflows/incremental.py`.

That file should own:

- base-ref and target-ref resolution
- baseline loading
- incremental run orchestration
- metadata persistence
- CLI/workflow-facing result emission

`diagram_analysis` should not own transport/payload concerns.

### 2. Remove or inline payload transport types

`diagram_analysis/incremental/payload.py` should either:

- be folded into `codeboarding_workflows/incremental.py`, or
- be deleted entirely if a separate payload layer is unnecessary

Transport models should remain at the boundary, not in the diagram domain layer.

### 3. Move tree-sitter semantic-diff logic to `static_analyzer/semantic_diff.py`

Move `diagram_analysis/incremental/semantic_diff.py` into `static_analyzer/semantic_diff.py`.

This module should own:

- tree-sitter parser selection
- old/new source loading for diff comparison
- cosmetic-change detection
- syntax-error detection
- source fingerprinting helpers
- method-signature fingerprinting

These are static-analysis utilities, not diagram-specific behavior.

### 4. Extract graph utilities into `static_analyzer/graph_query.py`

Move graph and symbol-resolution utilities out of `trace_planner.py` and `tracer.py` into `static_analyzer/graph_query.py`.

This module should own:

- cross-language method resolution helpers
- neighbor index construction from CFGs and call graphs
- graph traversal helpers
- weakly connected grouping helpers
- optional SCC condensation helpers if needed later

The diagram layer should consume these utilities rather than define them.

### 5. Consolidate deterministic delta logic into `diagram_analysis/incremental_updater.py`

Merge:

- `diagram_analysis/incremental/updater.py`
- `diagram_analysis/incremental/delta.py`
- `diagram_analysis/incremental_types.py`

into a single `diagram_analysis/incremental_updater.py`.

This file should contain:

- `MethodChange`
- `FileDelta`
- `IncrementalDelta`
- deterministic delta computation
- deterministic structural application logic
- file/component resolution helpers used during structure updates

This keeps delta build/apply as one cohesive concern.

### 6. Consolidate tracer logic into `diagram_analysis/incremental_tracer.py`

Merge:

- `diagram_analysis/incremental/tracer.py`
- the diagram-specific parts of `diagram_analysis/incremental/trace_planner.py`

into `diagram_analysis/incremental_tracer.py`.

The planner should be an implementation detail of the tracer, not a separate top-level subsystem.

### 7. Move patching into `diagram_analysis/analysis_patcher.py`

Move `agents/analysis_patcher.py` to `diagram_analysis/analysis_patcher.py`.

This code patches `AnalysisInsights`, so it belongs to the diagram domain.

As part of the move:

- remove the EASE + RFC 6902 JSON Patch design
- replace it with a simpler structured replacement model

### 8. Trim models into `diagram_analysis/incremental_models.py`

Move `diagram_analysis/incremental/models.py` to `diagram_analysis/incremental_models.py`, then trim it down.

Keep only:

- tracer config
- tracer stop reason
- trace result types
- diagram-facing incremental result/config types

Remove:

- payload transport types
- escalation enums that imply full-analysis fallback
- JSON-patch transport types

## Behavioral Plan

### 1. Keep the commit-based incremental seam

The implementation should continue to compare git commits using the current baseline model.

The baseline source of truth remains:

- `analysis.json.metadata.commit_hash`

The incremental path should continue to hang off `DiagramGenerator.generate_analysis_smart()` and the existing CLI seam.

### 2. Fix baseline persistence first

Full analysis must always persist `metadata.commit_hash` when saving `analysis.json`.

Without this:

- incremental has no stable base commit
- the tracer cannot compare against a trustworthy baseline
- the pipeline contract remains brittle

### 3. Keep structural updates deterministic

The deterministic updater runs before any semantic reasoning.

The flow should be:

1. load prior analysis
2. run static analysis / pre-analysis
3. detect file changes from the saved commit
4. compute file and method delta
5. apply the structural delta in memory
6. only then begin semantic filtering and tracing

The LLM should not own any of:

- `files`
- `methods_index`
- `file_methods`
- raw method add/modify/delete classification

### 4. Harden method seeding

Before building more tracer behavior, fix the known mixed-hunk weakness in method classification.

Requirement:

- if a brand-new method is introduced inside a mixed hunk, it must classify as `ADDED`
- it must not blur into `MODIFIED`

If the seed set is noisy, the tracer and patch scope will be noisy.

### 5. Add a strict tree-sitter cosmetic preprocessor

Cosmetic filtering should happen after deterministic delta computation and before any LLM tracing.

This preprocessor should remove changes that are provably:

- comment-only
- whitespace-only
- formatting-only
- docstring-only when equivalent to a cosmetic edit in context
- otherwise syntax-preserving cosmetic churn that tree-sitter can confidently prove

This filter is a preprocessing step, not a tracer decision.

The tracer should never spend tokens determining whether an edit is cosmetic if tree-sitter can prove it first.

### 6. Keep cosmetic filtering conservative

If any of the following occur:

- unsupported language
- parser load failure
- parse error that prevents confident comparison
- ambiguous structural comparison

then the change stays in the tracer seed set.

Only confident cosmetic proofs may suppress tracer inputs.

### 7. Cosmetic-only runs must still succeed

If all changed methods/files are filtered out as cosmetic:

- the run still succeeds
- structural state and baseline metadata are updated
- the semantic patch set is empty
- no fallback or escalation result is returned

### 8. Build a deterministic ownership index

Before patch scoping, build a method-aware ownership index over the loaded analysis.

It should provide:

- `method -> deepest owning component`
- `component -> subtree root`
- `component -> containing analysis scope`
- `file -> owning components`
- `component -> descendants`

File-level ownership alone is not sufficient for semantic patching.

### 9. Build semantic seeds from non-cosmetic delta

The tracer seed set should be formed from non-cosmetic:

- added methods
- modified methods
- deleted methods

Deleted methods come from the baseline snapshot. Added/modified methods come from fresh static analysis after `pre_analysis()`.

### 10. Implement the tracer as a CFG frontier walk

The tracer should work as an iterative graph exploration loop.

For each seed:

- resolve the method symbol
- get immediate callers and callees from the CFG/call graph
- ask the model which neighbors need to be inspected to determine impact
- fetch those method bodies and continue

The tracer should explore until one of:

- closure
- frontier exhaustion
- budget exhaustion

The tracer must reason about both:

- upstream impact
- downstream impact

### 11. Track both `visited_methods` and `impacted_methods`

The tracer output must distinguish:

- `visited_methods`: methods seen during tracing
- `impacted_methods`: methods judged materially relevant to documentation changes

Patch scope is determined from `visited_methods`.

Semantic emphasis inside the patcher can use `impacted_methods`.

Fast-path methods and deterministic shortcut cases still count as seen if they were part of the trace.

### 12. Start with a simple planner

Do not overfit the initial implementation to the full `new-iterative` planner complexity.

Start with:

- weakly connected seed groups
- file fallback grouping for unresolved graph cases
- bounded per-group tracing

If prompt size becomes a real constraint, SCC condensation can be added later through `static_analyzer/graph_query.py`.

### 13. Replace all full-analysis escape hatches

The incremental flow must not return:

- `REQUIRES_FULL_ANALYSIS`
- `SCOPED_REANALYSIS`
- "reexpand"
- "run full analysis"
- any equivalent fallback contract

Instead, uncertainty must become broader patch scope.

This applies to:

- unresolved symbols
- incomplete graph coverage
- syntax errors
- rename/copy ambiguity
- target-ref mismatch handling after baseline is established
- budget exhaustion
- partial semantic failure

### 14. Remove additive-only early exits

Purely additive changes must still go through semantic scope inference and patching.

This is required because:

- added files can be disconnected from the CFG
- a disconnected addition still needs semantic placement

### 15. Handle syntax errors conservatively

Syntax errors should not abort the semantic incremental run.

Instead:

- mark the region or file as non-traceable
- widen to the owning subtree
- patch that subtree anyway

### 16. Normalize rename and copy behavior

Rename/copy changes must not force escalation.

Handle them as:

- ownership-preserving rename where possible
- otherwise delete + add semantics
- widen scope if ownership is ambiguous

The key rule is still: patch conservatively, never escalate to full analysis.

### 17. Handle disconnected added files through synthetic scope inference

If an added file has no trace-visible CFG edges, it will not naturally produce traced methods.

In that case, infer a synthetic patch scope:

- search analyzed files in the same directory first
- if none, walk up to parent directories
- choose the nearest analyzed file by directory distance
- use that file's deepest owning component as the subtree root

This synthetic scope stands in for "methods touched by the trace" when the graph is disconnected.

### 18. Widen ambiguous synthetic scope to LCA

If equally near candidate files disagree on component ownership:

- compute their lowest common ancestor component
- patch that subtree

That replaces any old behavior that would have asked for a full reanalysis.

## Patcher Plan

### 1. Scope patching by trace evidence

The patcher should only patch:

- scopes whose methods were seen in `visited_methods`
- plus synthetic subtree scopes for disconnected added files

This is the main simplification over the existing `new-iterative` patcher design.

The trace tells us what is relevant, so the patcher does not need to re-derive global relevance.

### 2. Patch analysis scopes, not only sub-analyses

The patch target can be:

- the root analysis
- a sub-analysis rooted at a component
- a subtree inside a scope

The patching system must support all three cleanly.

### 3. Use structured replacements, not JSON Patch

Replace the EASE + RFC 6902 patching scheme with stable-ID structured replacements.

Each patch request/response should be keyed by:

- `component_id`
- stable relation keys

The patcher should update only:

- scope description
- impacted component descriptions
- impacted component `key_entities`
- relations touching impacted components

The patcher must not update:

- `files`
- `methods_index`
- `file_methods`

### 4. Merge deterministically by IDs

Patch merging should never depend on list order.

Components merge by:

- `component_id`

Relations merge by:

- a deterministic stable relation key

Anything not explicitly replaced remains unchanged.

### 5. Retry by widening scope, not by changing mode

If a patch attempt is incomplete or inconsistent:

- retry with a wider but still bounded scope
- do not switch to full analysis

Patch failure recovery must preserve the "always patch" contract.

## Workflow Ownership

### `codeboarding_workflows/incremental.py`

This boundary layer should own:

- loading existing analysis
- resolving refs/commits
- orchestrating the incremental run
- persisting updated metadata
- producing CLI/workflow-facing result objects

### `diagram_analysis/diagram_generator.py`

This remains the main domain entrypoint and should own the orchestration of:

1. pre-analysis
2. deterministic delta build
3. deterministic structural apply
4. cosmetic filtering
5. semantic tracing
6. scope derivation
7. semantic patching
8. saving the final analysis

### `static_analyzer/*`

The static-analysis layer should own:

- syntax-aware cosmetic detection
- graph utilities
- symbol lookup helpers

### `diagram_analysis/*`

The diagram layer should own:

- delta application to `AnalysisInsights`
- trace-to-scope translation
- patch generation and merge
- final analysis persistence

## Suggested Execution Order

### Phase 1. Restructure files without changing behavior

- create `codeboarding_workflows/incremental.py`
- create `static_analyzer/semantic_diff.py`
- create `static_analyzer/graph_query.py`
- create `diagram_analysis/incremental_models.py`
- create `diagram_analysis/incremental_tracer.py`
- create `diagram_analysis/analysis_patcher.py`
- consolidate types into `diagram_analysis/incremental_updater.py`
- add temporary compatibility imports if needed

The purpose of this phase is to land the flatter architecture first so later logic changes happen in the final file layout.

### Phase 2. Fix the baseline and deterministic substrate

- ensure full analysis always writes `metadata.commit_hash`
- harden commit/ref loading in the workflow layer
- fix mixed-hunk method classification
- ensure deterministic structural apply remains authoritative

### Phase 3. Extract static-analysis helpers

- move tree-sitter cosmetic logic into `static_analyzer/semantic_diff.py`
- move graph and symbol utilities into `static_analyzer/graph_query.py`
- rewire tracer code to depend on those modules

### Phase 4. Rewrite scope derivation and tracer contract

- add method-aware ownership indexing
- add `visited_methods`
- derive patch scopes from traced methods
- add disconnected-file synthetic subtree inference
- add LCA widening for ambiguous ownership

### Phase 5. Replace the patcher

- move patching into `diagram_analysis/analysis_patcher.py`
- remove EASE/JSON Patch
- implement structured replacements
- support root, sub-analysis, and subtree patching

### Phase 6. Remove escalation behavior

- delete fallback result kinds and payloads
- remove additive-only early exits
- replace syntax-error aborts with scope widening
- replace rename/copy aborts with conservative patching
- replace partial semantic failures with widened patch retries

### Phase 7. Delete compatibility shims and old package paths

- remove `diagram_analysis/incremental/`
- remove `diagram_analysis/incremental_types.py`
- remove `agents/analysis_patcher.py`
- clean up imports, tests, and dead models

## Test Plan

### Baseline and deterministic substrate

- full analysis writes `metadata.commit_hash`
- mixed hunks classify newly introduced methods as `ADDED`
- deterministic apply updates `files`, `methods_index`, and `file_methods` before semantic patching

### Cosmetic filtering

- comment-only edits never reach the tracer prompt
- formatting-only edits never reach the tracer prompt
- unsupported tree-sitter language remains in the semantic seed set
- cosmetic-only incremental run succeeds with an empty semantic patch set

### Tracer behavior

- upstream callers are explored until closure on a small call chain
- downstream callees are explored until closure on a small call chain
- `visited_methods` includes all methods seen during tracing
- `impacted_methods` is a subset used for semantic emphasis
- budget exhaustion widens scope and still patches

### Scope derivation

- traced methods patch only the owning components/subtrees
- root analysis can be selected as a patch target
- sub-analyses can be selected as patch targets
- added disconnected file patches the nearest directory subtree
- conflicting nearest owners widen to component LCA

### Failure-mode contract

- syntax-error path still patches
- rename path still patches
- copy path still patches
- unresolved symbol path still patches
- partial patch failure widens scope and still patches
- no incremental run with a baseline ever requests full analysis or re-expansion

## Acceptance Criteria

- The nested `diagram_analysis/incremental/` package is gone.
- The flatter file layout is in place and imports are clean.
- Cosmetic edits are filtered before any tracer LLM call.
- Structural mutation remains deterministic and precedes semantic work.
- Semantic patch scope is based on traced methods, not coarse file-level ownership.
- Added disconnected files still receive a semantic patch through subtree inference.
- All uncertainty paths widen scope instead of escalating execution mode.
- Every incremental run with an existing baseline ends in a patch or empty patch set.

## Non-Goals

- Do not import the full `new-iterative` CLI/workflow refactor unless a narrow piece is required.
- Do not make incremental a separate product architecture.
- Do not allow the LLM to mutate structural analysis fields.
- Do not keep both the nested and flat incremental layouts long-term.
