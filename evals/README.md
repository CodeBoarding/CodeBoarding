# CodeBoarding Evaluations

Comprehensive evaluation framework for assessing CodeBoarding's performance, reliability, and scalability across real-world projects. We run this only for major releases that impacts either of these.

## Overview

The evaluation system consists of three evaluation types:

- **Static Analysis**: Measures static analysis performance.
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

- `static-analysis-report.md`: Static analysis performance summary
- `end-to-end-report.md`: Full pipeline execution results with diagrams
- `scalability-report.md`: Scaling analysis with visualizations

### Artifacts

Pipeline artifacts are stored in `evals/artifacts/`:

- `evals/artifacts/<project>/`: Per-project analysis outputs
- `evals/artifacts/monitoring_results/runs/`: Monitoring data for each run
- `evals/artifacts/monitoring_results/reports/`: JSON evaluation results

## Requirements

- `ENABLE_MONITORING=true`: Required for collecting metrics (automatically set during evaluation)
- `REPO_ROOT`: Directory for cloned repositories (defaults to `repos`)

## Architecture

```
evals/
├── base.py              # Base evaluation class
├── cli.py               # Command-line interface
├── config.py            # Project configurations
├── schemas.py           # Data models
├── utils.py             # Utility functions
├── definitions/         # Evaluation implementations
│   ├── static_analysis.py
│   ├── end_to_end.py
│   └── scalability.py
└── reports/             # Generated reports
```

Each evaluation follows the pattern: **Run Pipeline → Extract Metrics → Generate Report**
