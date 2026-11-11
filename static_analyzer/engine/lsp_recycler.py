"""How much memory one language server may occupy, and how that is shared between engines."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from static_analyzer.engine.lsp_constants import (
    MAX_MEMORY_BUDGET,
    MEMORY_BUDGET_ENV_VAR,
    MEMORY_BUDGET_FRACTION,
    MIN_MEMORY_BUDGET,
    MIN_ENGINE_MEMORY_BUDGET,
)
from static_analyzer.engine.utils import total_ram_gb

logger = logging.getLogger(__name__)


def default_memory_budget() -> int:
    """Bytes the language servers up at once may occupy: a share of RAM, or of a cgroup v2 limit below it."""
    override = os.environ.get(MEMORY_BUDGET_ENV_VAR, "").strip()
    if override:
        try:
            return int(float(override) * 1024**2)
        except ValueError:
            logger.warning("Ignoring %s=%r: not a number of megabytes", MEMORY_BUDGET_ENV_VAR, override)
    memory = int((total_ram_gb() or 0) * 1024**3)
    try:
        limit = Path("/sys/fs/cgroup/memory.max").read_text().strip()
    except OSError:
        limit = "max"
    if limit.isdigit():
        memory = min(memory, int(limit)) if memory else int(limit)
    if not memory:
        return MIN_MEMORY_BUDGET
    return int(min(memory * MEMORY_BUDGET_FRACTION, MAX_MEMORY_BUDGET))


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
