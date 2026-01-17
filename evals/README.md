# CodeBoarding Evaluations

Comprehensive evaluation framework for assessing CodeBoarding's performance, reliability, and scalability across real-world projects. We run this only for major releases that impacts either of these.

## Overview

The evaluation system consists of four evaluation types:

- **Static Analysis**: Measures static analysis performance.
- **End-to-End**: Runs the full pipeline to produce diagrams of repos we are familiar with, to eyeball differences.
- **Scalability**: Analyzes system scaling characteristics with codebase size and depth-level.
- **Accuracy**: LLM-as-judge evaluation comparing generated diagrams against ground-truth datasets.

## Quick Start

```bash
# Run all evaluations
python -m evals.cli

# Run specific evaluation type
python -m evals.cli --type static
python -m evals.cli --type e2e
python -m evals.cli --type scalability
python -m evals.cli --type accuracy

# Generate reports from existing artifacts (skip pipeline execution)
python -m evals.cli --report-only

# Custom output directory
python -m evals.cli --output-dir custom/reports
```

## Configuration

Each evaluation task has its own configuration file:

- `evals/tasks/static_analysis/config.py`: Projects for static analysis evaluation
- `evals/tasks/end_to_end/config.py`: Projects for end-to-end pipeline evaluation
- `evals/tasks/scalability/config.py`: Projects for scalability analysis
- `evals/tasks/accuracy/config.py`: Projects and settings for accuracy evaluation

## Output

### Reports

Markdown reports are generated in `evals/reports/` (or custom `--output-dir`):

- `static-analysis-report.md`: Static analysis performance summary
- `end-to-end-report.md`: Full pipeline execution results with diagrams
- `scalability-report.md`: Scaling analysis with visualizations
- `accuracy-report.md`: Accuracy evaluation with LLM judge scores

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
├── schemas.py           # Shared data models
├── utils.py             # Utility functions
├── tasks/               # Evaluation task packages
│   ├── accuracy/        # Accuracy evaluation
│   │   ├── config.py    # Task-specific configuration
│   │   ├── datasets/    # Ground-truth datasets
│   │   ├── eval.py      # Main evaluation class
│   │   └── ...          # Supporting modules
│   ├── end_to_end/
│   │   ├── config.py
│   │   └── eval.py
│   ├── scalability/
│   │   ├── config.py
│   │   └── eval.py
│   └── static_analysis/
│       ├── config.py
│       └── eval.py
├── artifacts/           # Generated artifacts
└── reports/             # Generated reports
```

Each evaluation follows the pattern: **Run Pipeline → Extract Metrics → Generate Report**
