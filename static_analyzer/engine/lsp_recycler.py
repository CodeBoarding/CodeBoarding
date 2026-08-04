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
        self._disarmed = False
        self._done = 0
        self._total = 0
        logger.info("LSP recycler armed at a %s budget", format_bytes(self._budget))

    def note_progress(self, done: int, total: int) -> None:
        """Record how far the phase has got, for the restart log line."""
        self._done = done
        self._total = total

    def before_batch(self, warm_position: tuple[Path, int, int] | None = None) -> None:
        """Sample the server and restart it if it is over budget.

        One sample per batch, not per N batches: a single batch of solution-wide
        reference queries can allocate gigabytes, so a coarser interval sails
        past the budget between checks. Reading the process table costs
        milliseconds against a batch's own LSP round-trip.

        ``warm_position`` is the first position the caller is about to query. A
        restart uses it to rebuild the reference index on real work rather than
        on a synthetic probe (see ``_recycle``).
        """
        if self._disarmed:
            return
        used = self._server_rss()
        if used >= self._budget:
            self._recycle(used, warm_position)

    def _recycle(self, used: int, warm_position: tuple[Path, int, int] | None = None) -> None:
        # Carries the phase position deliberately: on a host that only surfaces
        # warnings this is the one periodic line proving the run is advancing.
        logger.warning(
            "LSP holding %s (budget %s) at position %d/%d — restarting it to release cached compilations",
            format_bytes(used),
            format_bytes(self._budget),
            self._done,
            self._total,
        )
        t_restart = time.monotonic()
        self._lsp.restart()
        # Blocks until the workspace is loaded again: a workspace-backed server
        # cannot answer a documentSymbol until it has read the project files.
        self._lsp.document_symbol(self._probe_file, timeout=self._probe_timeout)
        # A loaded workspace is not a warm reference index — that is built on the
        # first references query, which on a large solution materializes a
        # compilation per project. Pay for it here on a request we can give a
        # long timeout, using a position the caller is about to query anyway:
        # warming on a synthetic (0, 0) coordinate usually lands on no symbol at
        # all, so the server does nothing and the next real batch eats the stall.
        warm_file, warm_line, warm_char = warm_position or (self._probe_file, 0, 0)
        try:
            self._lsp.references(warm_file, warm_line, warm_char, timeout=self._probe_timeout)
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
