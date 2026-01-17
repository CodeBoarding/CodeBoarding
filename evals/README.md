# CodeBoarding Evaluations

Comprehensive evaluation framework for assessing CodeBoarding's performance, reliability, and scalability across real-world projects. We run this only for major releases that impacts either of these.

## Overview

The evaluation system consists of three evaluation types:

- **Static Analysis**: Measures static analysis performance using the `StaticAnalyzer` directly (fast, isolated analysis of code structure without LLM calls).
- **End-to-End**: Runs the full pipeline to produce diagrams of repos we are familiar with, to eyeball differences.
- **Scalability**: Analyzes system scaling characteristics with codebase size and depth-level.

## Quick Start

```bash
# Run all evaluations
python -m evals.cli

# Run specific evaluation type
python -m evals.cli --type static
python -m evals.cli --type e2e
python -m evals.cli --type scalability

# Generate reports from existing artifacts (skip pipeline execution)
python -m evals.cli --report-only

# Custom output directory
python -m evals.cli --output-dir custom/reports
```

## Configuration

Evaluation projects are configured in `evals/config.py`:

- `PROJECTS_STATIC_ANALYSIS`: Projects for static analysis evaluation
- `PROJECTS_E2E`: Projects for end-to-end pipeline evaluation
- `PROJECTS_SCALING`: Projects for scalability analysis (supports depth-level configuration)

## Output

### Reports

Markdown reports are generated in `evals/reports/` (or custom `--output-dir`):

- `static-analysis-report.md`: Static analysis performance summary (file counts, LOC per language)
- `end-to-end-report.md`: Full pipeline execution results with diagrams
- `scalability-report.md`: Scaling analysis with visualizations

### Artifacts

Pipeline artifacts are stored in `evals/artifacts/`:

- `evals/artifacts/<project>/`: Per-project analysis outputs
- `evals/artifacts/monitoring_results/runs/`: Monitoring data for each run
- `evals/artifacts/monitoring_results/reports/`: JSON evaluation results

## Requirements

- For **Static Analysis**: Only requires language servers to be available (fast, ~30 seconds per project)
- For **End-to-End**: Requires `ENABLE_MONITORING=true` and LLM API keys
- For **Scalability**: Same as End-to-End

## Architecture

```
evals/
├── base.py              # Base evaluation class
├── cli.py               # Command-line interface
├── config.py            # Project configurations
├── schemas.py           # Data models
├── utils.py             # Utility functions
├── definitions/         # Evaluation implementations
│   ├── static_analysis.py   # Direct StaticAnalyzer usage (no full pipeline)
│   ├── end_to_end.py
│   └── scalability.py
└── reports/             # Generated reports
```

## Static Analysis Evaluation

The static analysis eval runs the `StaticAnalyzer` directly to:
- Clone the repository
- Run language-specific static analysis (TypeScript LSP, Java JDTLS, etc.)
- Extract code statistics (file counts, lines of code by language)
- Generate performance report

This approach is much faster than the full pipeline and doesn't require LLM API keys or monitoring infrastructure.

Each evaluation follows the pattern: **Run Pipeline/Analyzer → Extract Metrics → Generate Report**
