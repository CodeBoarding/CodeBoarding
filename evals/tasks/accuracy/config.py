"""
Configuration for accuracy evaluation.

Contains project specifications, dataset settings, and scoring configuration.
"""

from evals.schemas import ProjectSpec

# Projects to evaluate for accuracy
PROJECTS = [
    ProjectSpec(
        name="markitdown",
        url="https://github.com/microsoft/markitdown",
        expected_language="Python",
        code_size="medium",
    ),
    ProjectSpec(
        name="pytorch_geometric",
        url="https://github.com/pytorch/pytorch_geometric",
        expected_language="Python",
        code_size="large",
    ),
]

# Depth levels to analyze for accuracy evaluation.
DEPTH_LEVELS = [1, 2]
