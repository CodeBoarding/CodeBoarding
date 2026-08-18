"""One summary event per run for failures the analysis absorbed.

A degradation is a failure that cost output without stopping the run: a batch
whose edges are gone, a symbol whose supertypes never resolved. Reporting each
one individually does not scale -- the hierarchy path alone visits ~3,900 class
symbols in a single abp engine, so a server that cannot answer typeHierarchy
would emit thousands of identical events and bury everything else in the
dashboard.

So they accumulate here and leave as a single event carrying counts per
category plus one worked example, which is what someone reading it actually
needs: how much was lost, of what kind, and what one instance looked like.
"""

import threading
from collections import Counter

_lock = threading.Lock()
_counts: Counter = Counter()
_samples: dict[str, str] = {}
_items: Counter = Counter()


def record(category: str, detail: str, *, items: int = 1) -> None:
    """Note one absorbed failure. ``items`` is what it cost (sites, symbols, edges)."""
    with _lock:
        _counts[category] += 1
        _items[category] += items
        _samples.setdefault(category, detail)


def summary() -> dict:
    """Counts, costs and one example per category; empty when the run was clean."""
    with _lock:
        if not _counts:
            return {}
        return {
            "degraded_categories": len(_counts),
            "degraded_events": sum(_counts.values()),
            "degraded_items": sum(_items.values()),
            "by_category": {
                category: {"occurrences": count, "items": _items[category], "example": _samples[category]}
                for category, count in _counts.most_common()
            },
        }


def reset() -> None:
    with _lock:
        _counts.clear()
        _items.clear()
        _samples.clear()
