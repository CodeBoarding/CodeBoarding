"""How much memory one language server may occupy, and how that is shared between engines."""

from __future__ import annotations

import logging
import os

from static_analyzer.engine.lsp_constants import (
    MAX_MEMORY_BUDGET,
    MEMORY_BUDGET_ENV_VAR,
    MEMORY_BUDGET_FRACTION,
    MIN_MEMORY_BUDGET,
    MIN_ENGINE_MEMORY_BUDGET,
)
from static_analyzer.engine.process_memory import physical_memory_bytes

logger = logging.getLogger(__name__)


def default_memory_budget() -> int:
    """Bytes a language server may occupy."""
    override = os.environ.get(MEMORY_BUDGET_ENV_VAR, "").strip()
    if override:
        try:
            return int(float(override) * 1024**2)
        except ValueError:
            logger.warning("Ignoring %s=%r: not a number of megabytes", MEMORY_BUDGET_ENV_VAR, override)
    physical = physical_memory_bytes()
    if not physical:
        return MIN_MEMORY_BUDGET
    return int(min(max(physical * MEMORY_BUDGET_FRACTION, MIN_MEMORY_BUDGET), MAX_MEMORY_BUDGET))


def per_engine_memory_budget(resident_engines: int) -> int:
    """Split the allowance across servers that may be resident at the same time.

    ``default_memory_budget`` is what *one* server may reach. Running several
    concurrently without dividing it lets N servers each grow to the whole
    allowance, so the concurrency bound would multiply peak memory rather than
    contain it -- the opposite of its purpose. The floor keeps a share large
    enough to be worth recycling against; below it a server would thrash.
    """
    total = default_memory_budget()
    if resident_engines <= 1:
        return total
    return max(MIN_ENGINE_MEMORY_BUDGET, total // resident_engines)
