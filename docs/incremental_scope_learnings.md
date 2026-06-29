# Incremental Scope Debugging Learnings

This captures the current findings from the MarkItDown depth-3 incremental evals so the follow-up can start from evidence instead of re-debugging the same loop.

## Context

- Eval project: `markitdown-l3` in `/Users/imilev/StartUp/CodeBoarding-evals`.
- Recent failed run inspected: `incremental/runs/markitdown-l3/20260629_103702`.
- Scenario: large diff adding MCP, OCR, Content Understanding, CSV, PDF/DOCX helper changes.
- Desired behavior: new architectural responsibilities should be detected, but unchanged existing methods should keep stable component ownership.

## What the latest changes already improved

- Recursive planning now logs scoped structural inputs in detail.
- Planner validation rejects empty `create_component` operations.
- Invalid planner decisions are logged/telemetry-tracked and filtered rather than blindly applied.
- Planner prompts now receive explicit routing facts.
- Structural diff rendering distinguishes:
  - `code_added=[...]`: method absent from the baseline CFG.
  - `existing_in_base=[...]`: method existed in the baseline and is only new to this scoped cluster/scope.
- Validation can reject `create_component` operations that only claim refs whose methods already existed in the baseline.

These changes made the failure easier to see, but did not fully fix the D3 churn.

## Important artifact finding

The static analysis cache does include the CFG and method cluster lineage:

- `baseline/static_analysis.pkl` is a `StaticAnalysisResults` with Python CFG and `method_cluster_paths`.
- `incremental/static_analysis.pkl` is also a `StaticAnalysisResults` with Python CFG and `method_cluster_paths`.

In the inspected run:

- Baseline CFG nodes: 135.
- Baseline method cluster paths: 133.
- Incremental method cluster paths: 266.

So we can isolate the issue without involving the LLM by comparing cached CFG lineage and analysis ownership directly.

## Core observation

Most baseline methods have different cluster paths after incremental analysis:

- Existing methods in both base and target: 133.
- Existing methods whose cached cluster paths changed: 123.

Examples:

```text
MarkItDown.convert_uri
  base: ['1.10', '1.4.1', '3']
  incr: ['1.16', '3']

MarkItDown.convert_url
  base: ['1.11', '1.4.2', '3']
  incr: ['1.16', '3']

DocumentConverter.accepts
  base: ['2.0', '2.1.0', '5']
  incr: ['2.19', '2.2.8', '5']
```

The final `analysis.json` also shows real ownership movement:

- Baseline methods in analysis: 134.
- Existing methods still present in incremental: 134.
- Existing methods with changed component owners: 57.

Top owner losses/gains in the failed run:

```text
Top lost owners:
2.1.2 Built-in & Plugin Bootstrapper     20
2.1   Registry & Plugin Manager          20
2     Dispatch & Registry                 8

Top gained owners:
2.3   Conversion Dispatcher              16
2.3.3 Stream Metadata Resolver           16
2.2   Type Inference Engine              15
2.2.2 Inference & Normalization Engine   12
3.2.3 Media Dispatch & Integration       12
```

This confirms the judge's complaint: existing methods are actually being moved between components, not just reported noisily.

## Most likely root cause

`diagram_analysis/diagram_generator.py::scoped_snapshot_for_scope` builds the recursive "old" scoped snapshot using:

```python
cfg = incremental_agent.static_analysis.get_cfg(language)
```

But `incremental_agent.static_analysis` is the target/incremental static analysis. For recursive scope diffs, the old side is therefore partly interpreted through target lineage.

That makes baseline-existing methods look newly introduced to a child scope when target clustering reorganizes them.

## Isolated proof from scope `2`

Using the same old child-scope methods from baseline analysis, grouping them by baseline lineage vs target lineage gives very different local clusters.

Baseline lineage:

```text
scope 2 old analysis qnames: 54

baseline lineage local cluster ids:
0: 1
1: 12
2: 12
3: 11
4..31: mostly singleton old child cluster ids
```

Target/final lineage:

```text
target/final lineage local cluster ids:
2: 29
3: 24
19: 6
20: 5
21: 13
22: 6
23: 3
24: 3
25: 3
27: 3
28: 2
...
```

This is the shuffle before the planner has a chance to make a meaningful decision.

## Follow-up direction

The next likely fix should be in scoped snapshot construction, not another prompt-only change:

1. Build recursive old scoped snapshots from `static_analysis.incremental_base_results`, not from target `static_analysis`.
2. Keep target CFG only for the new scoped cluster results.
3. Re-run the same isolation for scope `2`; existing baseline methods should not appear as `added_to_scope` just because target lineage changed.
4. Then rerun the D3 eval and inspect whether component churn drops before judging quality.

## Useful commands

```bash
cd /Users/imilev/StartUp/CodeBoarding-evals

# Rerun D3 full eval
rm -rf incremental/repos/markitdown-l3
.venv/bin/python -m incremental run --project markitdown-l3

# Judge only if structure passes
.venv/bin/python -m incremental judge --project markitdown-l3 -j 4
```
