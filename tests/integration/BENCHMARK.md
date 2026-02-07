# Static Analysis Benchmark

Compare static analysis performance between two CodeBoarding commits.

## Quick Start

**Compare two commits (all 6 repos, 3 iterations each):**
```bash
uv run python tests/integration/benchmark_static_analysis.py d813a36 6b9d951
```

**Single repo benchmark:**
```bash
uv run python tests/integration/benchmark_static_analysis.py HEAD HEAD~1 --repo mockito_java --iterations 1
```

**Using a pre-cloned target repo:**
```bash
uv run python tests/integration/benchmark_static_analysis.py HEAD HEAD~1 --repo mockito_java --repo-path /tmp/mockito
```

**With custom binary location (for JDTLS, LSP servers, etc.):**
```bash
uv run python tests/integration/benchmark_static_analysis.py HEAD HEAD~1 --binary-location /path/to/binaries
```

## What It Does

1. Clones CodeBoarding repo once into a temp directory
2. For each commit: checks out the code, runs static analysis on all 6 target repos
3. Measures: wall-clock time, LSP method times (hierarchy/outgoing/incoming/body), edge counts
4. Saves raw results to `benchmark_results/` as JSON
5. Prints comparison table with deltas

## Repos Included

- `codeboarding_python`: CodeBoarding repo itself
- `mockito_java`: Mockito test framework
- `prometheus_go`: Prometheus monitoring
- `excalidraw_typescript`: Excalidraw drawing app
- `wordpress_php`: WordPress CMS
- `lodash_javascript`: Lodash utility library

## Results

Results are saved to `benchmark_results/{repo}_{commit_a}_{commit_b}_{timestamp}.json` with raw iteration data for further analysis.

## Notes

- Timing is flaky due to LSP server variability; use `--iterations 3+` for stable averages
- First run clones large repos (takes time), subsequent runs reuse clones
- Workers run in subprocesses to get fresh imports of the checked-out commit's code
