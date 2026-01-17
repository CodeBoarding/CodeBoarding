"""
Scalability evaluation for CodeBoarding.

Analyzes system scaling characteristics with codebase size and depth-level,
generating visual plots to identify trends and bottlenecks.
"""

from evals.tasks.scalability.eval import ScalabilityEval

__all__ = ["ScalabilityEval"]
