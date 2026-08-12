"""Reusable workflow entry points for CodeBoarding analysis.

Public surface:

- :mod:`codeboarding_workflows.analysis` — the three scopes
  (``run_full``, ``run_partial``, ``run_incremental``) plus the shared
  ``run_incremental_workflow`` kernel.
- :mod:`codeboarding_workflows.sources` — local vs. remote repo materialization
"""

from codeboarding_workflows.analysis import run_full, run_incremental, run_incremental_workflow, run_partial
from codeboarding_workflows.orchestration import run_analysis_pipeline

__all__ = ["run_analysis_pipeline", "run_full", "run_incremental", "run_incremental_workflow", "run_partial"]
