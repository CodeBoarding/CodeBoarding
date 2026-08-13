"""Physical-location identity for call-graph symbols.

The LSP can emit several qualified names for one physical symbol (two tsconfig
projects resolving the same file under different roots). ``LocationKey`` is what
lets the graph recognise those as one node. See issue #471 for moving the
canonicalization itself out of ``CallGraph.add_node`` into an explicit pass here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocationKey:
    """Hashable key identifying a symbol's physical location in the source tree."""

    file_path: str
    line_start: int
    line_end: int
    node_type: int
    col_start: int = 0
