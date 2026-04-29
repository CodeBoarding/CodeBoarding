"""Reusable workflow entry points for CodeBoarding analysis.

Public surface:

- :mod:`codeboarding_workflows.analysis` — the three scopes
  (``run_full``, ``run_partial``, ``run_incremental``)
- :mod:`codeboarding_workflows.sources` — local vs. remote repo materialization
- :mod:`codeboarding_workflows.markdown` — docs rendering from ``analysis.json``
"""

from codeboarding_workflows.analysis import run_full, run_incremental, run_partial
from codeboarding_workflows.incremental import run_incremental_workflow
from codeboarding_workflows.orchestration import run_analysis_pipeline

__all__ = ["run_analysis_pipeline", "run_full", "run_incremental", "run_incremental_workflow", "run_partial"]
