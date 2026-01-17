"""
End-to-end evaluation for CodeBoarding.

Runs the full pipeline to produce diagrams and evaluates overall success
rates and resource consumption.
"""

from evals.tasks.end_to_end.eval import EndToEndEval

__all__ = ["EndToEndEval"]
