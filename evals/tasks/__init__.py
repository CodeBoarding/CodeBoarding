"""
Evaluation tasks for CodeBoarding.

This package contains implementations for different evaluation types,
each with its own config and datasets:

- accuracy/: LLM-as-judge accuracy evaluation against ground truth
- end_to_end/: Full pipeline execution evaluation
- scalability/: Scaling characteristics analysis
- static_analysis/: Static analysis performance evaluation

Each task is a self-contained package with:
- eval.py: Main evaluation class
- config.py: Task-specific configuration
- datasets/: Task-specific datasets (if applicable)
- Supporting modules (if needed)
"""

from evals.tasks.accuracy import (
    AccuracyEval,
    LevelTwoFromLevelOneEval,
    run_accuracy_eval,
    run_level2_from_level1_eval,
)
from evals.tasks.end_to_end import EndToEndEval
from evals.tasks.scalability import ScalabilityEval
from evals.tasks.static_analysis import StaticAnalysisEval

__all__ = [
    # Accuracy evaluation
    "AccuracyEval",
    "run_accuracy_eval",
    "LevelTwoFromLevelOneEval",
    "run_level2_from_level1_eval",
    # Other evaluations
    "EndToEndEval",
    "ScalabilityEval",
    "StaticAnalysisEval",
]
