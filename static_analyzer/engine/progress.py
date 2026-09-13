"""Progress logging for long-running analysis phases.

A line every ``PROGRESS_INTERVAL_SEC`` while a phase runs, and one when it finishes, so a phase
shorter than the interval logs only its completion.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Seconds between progress lines while a phase runs.
PROGRESS_INTERVAL_SEC = 30.0


class ProgressLogger:
    """Logs a phase's progress on a fixed interval, and its completion."""

    def __init__(self, phase: str, total: int, *, unit: str = "item") -> None:
        self._phase = phase
        self._total = max(total, 1)
        self._unit = unit
        self._done = 0
        self._t_start = time.monotonic()
        self._t_last_log = self._t_start
        self._extra: dict[str, object] = {}

    def set_postfix(self, **kwargs: object) -> None:
        self._extra = kwargs

    def update(self, n: int = 1) -> None:
        self._done = min(self._done + n, self._total)
        now = time.monotonic()
        # The last step is left to ``finish``, so completion is logged once.
        if self._done < self._total and now - self._t_last_log >= PROGRESS_INTERVAL_SEC:
            self._log(now)

    def finish(self) -> None:
        """Log the completion line (call after the loop)."""
        self._done = self._total
        self._log(time.monotonic())

    def _log(self, now: float) -> None:
        extra_str = ""
        if self._extra:
            extra_str = " | " + ", ".join(f"{k}={v}" for k, v in self._extra.items())
        logger.info(
            "%s: %d%% (%d/%d %ss, %.1fs elapsed%s)",
            self._phase,
            int(self._done * 100 / self._total),
            self._done,
            self._total,
            self._unit,
            now - self._t_start,
            extra_str,
        )
        self._t_last_log = now
