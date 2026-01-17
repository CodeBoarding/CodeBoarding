"""
Accuracy evaluation for CodeBoarding diagram generation.

Compares generated diagrams against a curated ground-truth dataset
using an LLM-as-judge approach to score structural similarity.
"""

from evals.tasks.accuracy.eval import (
    AccuracyEval,
    run_accuracy_eval,
)
from evals.tasks.accuracy.level_two import (
    LevelTwoFromLevelOneEval,
    run_level2_from_level1_eval,
)

__all__ = [
    "AccuracyEval",
    "run_accuracy_eval",
    "LevelTwoFromLevelOneEval",
    "run_level2_from_level1_eval",
]
