"""Retry policy primitives for LLM calls."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryAction(Enum):
    RETRY = auto()  # sleep, then retry
    RETRY_NOW = auto()  # retry immediately without sleeping
    GIVE_UP = auto()  # re-raise the exception


@dataclass(frozen=True)
class RetryDecision:
    """Outcome of classifying an exception.

    ``backoff_s`` is only consulted when ``action == RETRY``.
    """

    action: RetryAction
    backoff_s: float = 0.0


def default_backoff(attempt: int, *, initial_s: float, multiplier: float, max_s: float | None) -> float:
    """Standard exponential backoff: ``initial * multiplier**attempt`` clamped to ``max_s``."""
    delay = initial_s * (multiplier**attempt)
    return min(delay, max_s) if max_s is not None else delay


def _default_classify(_exc: Exception, _attempt: int) -> RetryDecision:
    return RetryDecision(action=RetryAction.RETRY)


def with_retries(
    fn: Callable[[], T],
    *,
    max_attempts: int,
    classify: Callable[[Exception, int], RetryDecision] = _default_classify,
    on_exhausted: Callable[[Exception], T] | None = None,
    log_prefix: str = "LLM call",
) -> T:
    """Run *fn* with up to *max_attempts* attempts under the given classification.

    On each exception the *classify* callback decides whether to retry (with
    sleep), retry immediately, or give up. If every attempt raises,
    *on_exhausted* is invoked with the last exception — its return value is
    propagated. When *on_exhausted* is ``None``, the last exception is
    re-raised.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            decision = classify(exc, attempt)
            if decision.action == RetryAction.GIVE_UP:
                raise
            if attempt >= max_attempts - 1:
                break
            if decision.action == RetryAction.RETRY:
                logger.warning(
                    "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                    log_prefix,
                    attempt + 1,
                    max_attempts,
                    exc,
                    decision.backoff_s,
                )
                time.sleep(decision.backoff_s)
            else:  # RETRY_NOW
                logger.warning(
                    "%s failed (attempt %d/%d): %s; retrying immediately",
                    log_prefix,
                    attempt + 1,
                    max_attempts,
                    exc,
                )

    assert last_exc is not None  # unreachable — loop always ran at least once
    logger.error("%s failed after %d attempts: %s", log_prefix, max_attempts, last_exc)
    if on_exhausted is None:
        raise last_exc
    return on_exhausted(last_exc)
