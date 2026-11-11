"""Retry policy primitives for LLM calls."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TypeVar

from agents.llm_errors import is_transient_llm_error, retry_after_seconds

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


#: Attempts per model request. Why these numbers: the full-jitter ceilings run 4, 8, 16 and 32 s, so the
#: four retries span up to a minute — long enough for a slot to free up behind the hosted proxy's concurrency cap
#: (one held slot is one model call in flight, often tens of seconds) without doubling a run that is failing anyway.
LLM_MAX_ATTEMPTS = 5
LLM_BACKOFF_INITIAL_S = 4.0
LLM_BACKOFF_MAX_S = 60.0


def classify_llm_error(exc: Exception, attempt: int) -> RetryDecision:
    """Retry a transient provider failure after the wait its ``Retry-After`` asks for, else a full-jitter backoff.

    Why full jitter: the parallel calls one burst throttles would otherwise all retry in the same second and
    collide again.
    """
    if not is_transient_llm_error(exc):
        return RetryDecision(action=RetryAction.GIVE_UP)
    delay = retry_after_seconds(exc)
    if delay is None:
        ceiling = default_backoff(attempt, initial_s=LLM_BACKOFF_INITIAL_S, multiplier=2.0, max_s=LLM_BACKOFF_MAX_S)
        delay = random.uniform(0.0, ceiling)
    return RetryDecision(action=RetryAction.RETRY, backoff_s=min(delay, LLM_BACKOFF_MAX_S))


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
