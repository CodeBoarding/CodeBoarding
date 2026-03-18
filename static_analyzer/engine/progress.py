"""Adaptive progress logging for long-running analysis phases.

Replaces tqdm with logger.info calls that adapt to repo size and elapsed time.
Rules:
- Log at most every MIN_INTERVAL_SEC seconds (default 5s).
- Log at every percentage step that crosses a milestone boundary (computed from
  total: 1% for large totals, 5-10% for small ones, etc.).
- Always log the final 100% completion line.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Minimum seconds between progress log lines.
MIN_INTERVAL_SEC = 5.0


class ProgressLogger:
    """Tracks and logs progress for a phase with adaptive frequency."""

    def __init__(self, phase: str, total: int, *, unit: str = "item") -> None:
        self._phase = phase
        self._total = max(total, 1)
        self._unit = unit
        self._done = 0
        self._t_start = time.monotonic()
        self._t_last_log = 0.0  # epoch=0 so first meaningful update logs
        self._pct_last_logged = -1

        # Decide milestone step: for very small totals use coarser steps.
        if self._total >= 200:
            self._pct_step = 1  # log every 1%
        elif self._total >= 50:
            self._pct_step = 5
        else:
            self._pct_step = 10

        self._extra: dict[str, object] = {}

    def set_postfix(self, **kwargs: object) -> None:
        self._extra = kwargs

    def update(self, n: int = 1) -> None:
        self._done = min(self._done + n, self._total)
        pct = int(self._done * 100 / self._total)
        now = time.monotonic()

        crossed_milestone = (
            (pct // self._pct_step) > (self._pct_last_logged // self._pct_step)
            if self._pct_last_logged >= 0
            else pct >= self._pct_step
        )
        elapsed_since_log = now - self._t_last_log

        if crossed_milestone and elapsed_since_log >= MIN_INTERVAL_SEC:
            self._log(pct, now)

    def finish(self) -> None:
        """Log the final 100% line (call after the loop)."""
        self._done = self._total
        self._log(100, time.monotonic())

    def _log(self, pct: int, now: float) -> None:
        elapsed = now - self._t_start
        extra_str = ""
        if self._extra:
            extra_str = " | " + ", ".join(f"{k}={v}" for k, v in self._extra.items())
        logger.info(
            "%s: %d%% (%d/%d %ss, %.1fs elapsed%s)",
            self._phase,
            pct,
            self._done,
            self._total,
            self._unit,
            elapsed,
            extra_str,
        )
        self._t_last_log = now
        self._pct_last_logged = pct
