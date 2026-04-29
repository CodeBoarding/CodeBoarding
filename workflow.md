# Incremental Workflow

This document defines the intended normal incremental workflow for semantic incremental analysis.

## Scope

This is the workflow once a baseline `analysis.json` already exists and includes a valid `metadata.commit_hash`.

Bootstrap is outside this contract:
- No baseline yet: run baseline creation first.

## Invariants

- Incremental uses commit-based git diffing against the saved baseline.
- Structural updates happen deterministically before semantic patching.
- Tree-sitter cosmetic filtering happens before any semantic tracer LLM call.
- Uncertainty widens patch scope.
- A normal incremental run never ends in `requires_full_analysis`, `scoped_reanalysis`, `reexpand`, or an equivalent fallback.

## Workflow

1. Baseline check
- If `analysis.json` and `metadata.commit_hash` exist, continue.
- If no baseline exists, exit the normal incremental path and create the baseline first.

2. Git diff
- Compare the saved baseline commit against the current target.
- If the diff is empty, write updated incremental metadata and exit with `no_changes`.
- If the diff is non-empty, continue.

3. Structural delta
- Build the deterministic file and method delta from git changes.
- File-level cases are `ADDED`, `MODIFIED`, and `DELETED`.
- Method seeding must classify genuinely new methods as `ADDED`, even inside mixed hunks.

4. Cosmetic preprocessing
- Run tree-sitter cosmetic filtering on changed files and methods.
- Remove comment-only, whitespace-only, formatting-only, and other provably cosmetic edits from the semantic tracer seed set.
- If every change is cosmetic, save the updated baseline and exit with `cosmetic_only`.
- If any non-cosmetic change remains, continue.

5. Trace seeding
- Seed the semantic tracer from the remaining changed methods.
- If an added file has no graph-connected methods, do not abort. Carry it forward for synthetic subtree scope inference.

6. Semantic tracing
- Explore upstream callers and downstream callees through CFG neighbors.
- Continue until closure, frontier exhaustion, or budget exhaustion.
- Track both:
  - `visited_methods`: methods seen during trace; this defines semantic patch scope.
  - `impacted_methods`: methods judged to materially affect documentation.

7. Conservative widening
- If symbol resolution is incomplete, syntax is problematic, graph coverage is incomplete, or the trace budget is exhausted, widen scope conservatively and continue.
- These are patch paths, not abort paths.

8. Scope derivation
- Normal case: derive patch scopes from the components that own `visited_methods`.
- Added disconnected file case: derive a synthetic patch scope from the nearest analyzed file in the same directory, then nearest parent directory.
- Ambiguous ownership case: widen to the lowest common ancestor subtree.

9. Scoped patching
- If there are no impacted scopes after filtering, save the structurally updated analysis and exit with an empty semantic patch set.
- If impacted scopes exist, patch only those scopes.
- Structural fields stay deterministic and are not LLM-owned.
- Semantic patching is limited to analysis descriptions, component descriptions, key entities, and touched relations.

10. Persistence
- Save the patched analysis.
- Advance the baseline commit and incremental metadata.

## Terminal States

Normal incremental should end in exactly one of these states:

- `no_changes`
- `cosmetic_only`
- `patched_from_trace`
- `patched_from_conservative_widening`

## Branches That Should Not Exist

These outcomes are not part of the intended normal workflow:

- `requires_full_analysis`
- `scoped_reanalysis`
- `reexpand`
- syntax-error abort
- rename or copy abort
- additive-only early exit without scope inference

## Edge Cases

### Added files without CFG edges

- These files may produce no semantic trace hits.
- They still need semantic patching.
- Their patch scope is inferred from the subtree that the nearest analyzed file belongs to.

### Ambiguous nearest ownership

- If equally near files disagree on ownership, widen to the lowest common ancestor component.

### Cosmetic-only runs

- Cosmetic-only runs are still successful incremental runs.
- They update metadata and the saved baseline.
- They do not invoke the semantic tracer LLM.
