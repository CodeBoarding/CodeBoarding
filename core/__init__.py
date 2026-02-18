"""
CodeBoarding plugin infrastructure.

Usage:
    from core import get_registries, load_plugins

    registries = get_registries()
    load_plugins(registries)

Type aliases for plugin contracts are available in core.protocols:
    from core.protocols import HealthCheckFunc, ToolFactory
"""

from core.plugin_loader import load_plugins
from core.protocols import HealthCheckFunc, ToolFactory
from core.registry import Registry


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


__all__ = [
    "Registry",
    "Registries",
    "get_registries",
    "reset_registries",
    "load_plugins",
]
