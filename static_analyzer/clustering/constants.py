"""Clustering-side constants.

These live here rather than in ``static_analyzer.constants`` because they are
typed with ``EdgeKind``, which sits above that module in the import order.
"""

from __future__ import annotations

from static_analyzer.cfg.edge import EdgeKind

# Folded in on top of call edges. CONTAINS and INHERITS reconnect the symbols the
# call graph leaves isolated — constructors, dunders, interface methods. No engine
# emits TYPEREF/IMPORT yet.
CLUSTERING_REFERENCE_KINDS: tuple[EdgeKind, ...] = (EdgeKind.CONTAINS, EdgeKind.INHERITS)
