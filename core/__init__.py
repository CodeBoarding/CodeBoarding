"""
CodeBoarding plugin infrastructure.

Usage:
    from core import get_registries, load_plugins

    registries = get_registries()
    load_plugins(registries)

Type aliases for plugin contracts are available in core.protocols:
    from core.protocols import HealthCheckFunc, ToolFactory
"""

import logging
from typing import TYPE_CHECKING

from core.plugin_loader import load_plugins
from core.protocols import HealthCheckFunc, ToolFactory
from core.registry import Registry

if TYPE_CHECKING:
    from agents.tools.base import BaseRepoTool, RepoContext
    from health.models import HealthCheckConfig, StandardCheckSummary, CircularDependencyCheck
    from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class Registries:
    """Namespace holding all plugin registries.

    Type contracts for each registry are defined in core.protocols.
    """

    def __init__(self) -> None:
        self.health_checks: Registry[HealthCheckFunc] = Registry("health_checks")
        self.tools: Registry[ToolFactory] = Registry("tools")


_registries: Registries | None = None


def get_registries() -> Registries:
    """Return the global Registries singleton, creating it on first call."""
    global _registries
    if _registries is None:
        _registries = Registries()
    return _registries


def reset_registries() -> None:
    """Reset registries to empty state. For testing only."""
    global _registries
    _registries = None


def run_plugin_health_checks(
    static_analysis: "StaticAnalysisResults",
    language: str,
    config: "HealthCheckConfig",
) -> "list[StandardCheckSummary | CircularDependencyCheck]":
    """Run all registered plugin health checks for a language and return their summaries.

    Failures in individual plugins are logged and skipped so a broken plugin
    cannot take down the entire health check pipeline.
    """
    summaries: list = []
    for name, check_func in get_registries().health_checks.all().items():
        try:
            summaries.extend(check_func(static_analysis, language, config))
        except Exception:
            logger.exception(f"Plugin health check '{name}' failed for language '{language}'")
    return summaries


def load_plugin_tools(context: "RepoContext") -> "list[BaseRepoTool]":
    """Instantiate and return all tools registered by plugins for the given repo context.

    Failures in individual plugin factories are logged and skipped so a broken
    plugin cannot prevent other tools from loading.
    """
    tools: list = []
    for name, factory in get_registries().tools.all().items():
        try:
            tools.extend(factory(context))
        except Exception:
            logger.warning(f"Plugin tool factory '{name}' failed", exc_info=True)
    return tools


__all__ = [
    "HealthCheckFunc",
    "Registries",
    "Registry",
    "ToolFactory",
    "get_registries",
    "load_plugin_tools",
    "load_plugins",
    "reset_registries",
    "run_plugin_health_checks",
]
