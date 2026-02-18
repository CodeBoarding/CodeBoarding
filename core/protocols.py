"""Type definitions for plugin extension points."""

from collections.abc import Callable
from typing import Any

from health.models import (
    CircularDependencyCheck,
    HealthCheckConfig,
    StandardCheckSummary,
)
from static_analyzer.analysis_result import StaticAnalysisResults

# A health check function matching the signature of built-in checks in health/runner.py.
# Receives (static_analysis, language, config) and returns a list of check summaries.
HealthCheckFunc = Callable[
    [StaticAnalysisResults, str, HealthCheckConfig],
    list[StandardCheckSummary | CircularDependencyCheck],
]

# A tool factory: given a RepoContext, returns a list of BaseRepoTool instances.
# Uses Any to avoid circular dependency with agents/ package.
# Plugin authors import RepoContext and BaseRepoTool from agents.tools.base directly.
ToolFactory = Callable[[Any], list[Any]]
