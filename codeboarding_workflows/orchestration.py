"""Pipeline orchestration — the template that glues source + scope + run-context.

The two consumers (CLI, GitHub Action) both need the same lifecycle:

1. enter a source context manager (local or remote)
2. resolve a :class:`RunContext` against the materialized repo
3. run a scope callable that receives the source + run context
4. finalize the run context even on failure
5. honor "cache hit" short-circuits from the source

This module owns that lifecycle so callers can't drift out of sync.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TypeVar

from codeboarding_workflows.sources import SourceContext
from diagram_analysis import RunContext

T = TypeVar("T")
Scope = Callable[[SourceContext, RunContext], T]


def run_analysis_pipeline(
    source: AbstractContextManager[SourceContext | None],
    scope: Scope[T],
    *,
    reuse_latest_run_id: bool = False,
) -> T | None:
    """Materialize *source*, run *scope* under a RunContext, finalize on exit.

    Returns ``None`` when *source* yields ``None`` (cache hit). Otherwise
    returns the scope's return value. The RunContext is finalized in a
    ``finally`` block so it's released even if the scope raises.
    """
    with source as src:
        if src is None:
            return None
        run_context = RunContext.resolve(
            repo_dir=src.repo_path,
            project_name=src.project_name,
            reuse_latest_run_id=reuse_latest_run_id,
        )
        try:
            return scope(src, run_context)
        finally:
            run_context.finalize()
