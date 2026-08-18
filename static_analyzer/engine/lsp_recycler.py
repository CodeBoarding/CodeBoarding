"""Bounds language-server memory during the references phase by restarting it.

Answering ``textDocument/references`` across a large workspace makes Roslyn (and
comparable engines) materialize a compilation per project and keep every one.
Growth is linear in positions queried: on bitwarden/server csharp-ls climbed
past 11GB and the run was OOM-killed on a 16GB runner. GC tuning halves the
slope but the line still has no ceiling, so a big enough repo always wins.

Restarting the server between batches drops the whole accumulation and costs one
workspace reload. It is only sound for servers that read documents from the
project itself rather than from our ``didOpen`` overlays -- those answer position
queries for files we never opened, so a fresh process is equivalent to the old
one. Adapters declare that via ``LanguageAdapter.workspace_owns_documents``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import (
    MAX_MEMORY_BUDGET,
    MEMORY_BUDGET_ENV_VAR,
    MEMORY_BUDGET_FRACTION,
    MIN_MEMORY_BUDGET,
    PRESSURE_FRACTION,
    PRESSURED_BATCH_DIVISOR,
    MIN_ENGINE_MEMORY_BUDGET,
)
from static_analyzer.engine.process_memory import format_bytes, physical_memory_bytes, process_tree_rss

logger = logging.getLogger(__name__)


def default_memory_budget() -> int:
    """Bytes a language server may occupy before it gets recycled."""
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


class LSPRecycler:
    """Restarts a language server whose process tree outgrows the budget."""

    def __init__(
        self,
        lsp: LSPClient,
        probe_file: Path,
        probe_timeout: int,
        budget_bytes: int = 0,
    ) -> None:
        self._lsp = lsp
        self._probe_file = probe_file
        self._probe_timeout = probe_timeout
        self._budget = budget_bytes or default_memory_budget()
        self.recycle_count = 0
        self.shrunk_batches = 0
        self._disarmed = False
        logger.info("LSP recycler armed at a %s budget", format_bytes(self._budget))

    def before_batch(self, full_batch_size: int) -> int:
        """Sample the server, restart it if over budget, and size the next batch.

        One sample per batch, not per N batches: a single batch of solution-wide
        reference queries can allocate gigabytes, so a coarser interval sails
        past the budget between checks. Reading the process table costs
        milliseconds against a batch's own LSP round-trip.

        Returns how many positions the next batch may carry. A batch is one
        round-trip we cannot interrupt, so its own growth is the floor under any
        bound we can offer; near the ceiling we carry fewer queries at once to
        keep that floor low.
        """
        if self._disarmed:
            return full_batch_size
        used = self._server_rss()
        if used >= self._budget:
            self._recycle(used)
            return full_batch_size
        if used < self._budget * PRESSURE_FRACTION:
            return full_batch_size
        self.shrunk_batches += 1
        return max(1, full_batch_size // PRESSURED_BATCH_DIVISOR)

    def _recycle(self, used: int) -> None:
        logger.warning(
            "LSP holding %s (budget %s) — restarting it to release cached compilations",
            format_bytes(used),
            format_bytes(self._budget),
        )
        t_restart = time.monotonic()
        self._lsp.restart()
        # Blocks until the workspace is loaded again: a workspace-backed server
        # cannot answer a documentSymbol until it has read the project files.
        self._lsp.document_symbol(self._probe_file, timeout=self._probe_timeout)
        # A loaded workspace is not a warm reference index — that is built on the
        # first references query. Pay for it here, on a request we can give a long
        # timeout, rather than losing a whole batch of real positions to the
        # per-request deadline. Measured: recycling without this tripled the
        # "Timeout waiting for references request" warnings.
        try:
            self._lsp.references(self._probe_file, 0, 0, timeout=self._probe_timeout)
        except Exception as exc:
            logger.debug("Post-restart references warmup failed (non-fatal): %s", exc)
        self.recycle_count += 1
        reloaded = self._server_rss()
        logger.info(
            "LSP restarted in %.0fs: %s -> %s (recycle #%d)",
            time.monotonic() - t_restart,
            format_bytes(used),
            format_bytes(reloaded),
            self.recycle_count,
        )
        if reloaded >= self._budget:
            # A workspace whose freshly-loaded footprint already exceeds the
            # budget cannot be helped by restarting: every batch would trigger
            # another reload and the phase would make no progress. Say so and
            # get out of the way — the budget is a fraction of RAM, so running
            # on may still fit, and a livelock certainly will not.
            self._disarmed = True
            logger.warning(
                "Loading this workspace alone needs %s, at or above the %s budget — memory can no longer be bounded. "
                "Raise %s or analyze on a host with more RAM.",
                format_bytes(reloaded),
                format_bytes(self._budget),
                MEMORY_BUDGET_ENV_VAR,
            )

    def _server_rss(self) -> int:
        pid = self._lsp.pid
        if pid is None:
            return 0
        return process_tree_rss(pid)
