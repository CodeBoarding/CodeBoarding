"""Reusable workflow entry points for CodeBoarding analysis.

Public surface:

- :mod:`codeboarding_workflows.analysis` — the two scopes
  (``run_full``, ``run_partial``).
- :mod:`codeboarding_workflows.sources` — local vs. remote repo materialization
- :mod:`codeboarding_workflows.markdown` — docs rendering from ``analysis.json``
"""

from codeboarding_workflows.analysis import run_full, run_partial
from codeboarding_workflows.orchestration import run_analysis_pipeline

__all__ = ["run_analysis_pipeline", "run_full", "run_partial"]
