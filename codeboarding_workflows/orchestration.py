"""Materialize analysis sources and invoke full or partial workflows."""

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
) -> T | None:
    """Materialize *source* and run *scope* under a RunContext.

    Returns ``None`` when *source* yields ``None`` (cache hit). Otherwise
    returns the scope's return value.
    """
    with source as src:
        if src is None:
            return None
        run_context = RunContext.resolve(project_name=src.project_name)
        return scope(src, run_context)
