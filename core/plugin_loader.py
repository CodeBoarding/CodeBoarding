"""Discover and load plugins via Python entry points."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Registries

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "codeboarding.plugins"


def load_plugins(registries: Registries) -> list[str]:
    """
    Discover installed plugins and call their init functions.

    Each plugin must expose an entry point in the 'codeboarding.plugins' group.
    The entry point must resolve to a callable with signature:
        def plugin_init(registries: Registries) -> None

    Returns:
        List of successfully loaded plugin names.
    """
    loaded: list[str] = []
    discovered = entry_points(group=ENTRY_POINT_GROUP)

    for ep in discovered:
        try:
            logger.info(f"Loading plugin: {ep.name} ({ep.value})")
            init_func = ep.load()
            init_func(registries)
            loaded.append(ep.name)
            logger.info(f"Plugin '{ep.name}' loaded successfully")
        except Exception:
            logger.exception(f"Failed to load plugin '{ep.name}'")

    if loaded:
        logger.info(f"Loaded {len(loaded)} plugin(s): {loaded}")
    else:
        logger.debug("No plugins discovered")

    return loaded
