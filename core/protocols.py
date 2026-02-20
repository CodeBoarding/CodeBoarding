"""Type definitions for plugin extension points."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from static_analyzer.analysis_result import StaticAnalysisResults

if TYPE_CHECKING:
    from agents.tools.base import BaseRepoTool, RepoContext
    from health.models import (
        CircularDependencyCheck,
        HealthCheckConfig,
        StandardCheckSummary,
    )

# A health check function matching the signature of built-in checks in health/runner.py.
# Receives (static_analysis, language, config) and returns a list of check summaries.
HealthCheckFunc = Callable[
    [StaticAnalysisResults, str, "HealthCheckConfig"],
    "list[StandardCheckSummary | CircularDependencyCheck]",
]

# A tool factory: given a RepoContext, returns a list of BaseRepoTool instances.
# Plugin authors import RepoContext and BaseRepoTool from agents.tools.base directly.
ToolFactory = Callable[["RepoContext"], "list[BaseRepoTool]"]
