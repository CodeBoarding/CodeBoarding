"""Call-graph structure: nodes, call edges, reference edges and subgraph derivation.

``call_graph`` holds the graph itself, ``edge`` the edge types, and ``canonical``
the physical-location identity used to dedup LSP qualified-name aliases.
"""

from static_analyzer.cfg.call_graph import CallGraph
from static_analyzer.cfg.canonical import LocationKey
from static_analyzer.cfg.edge import Edge, EdgeKind, ReferenceEdge

__all__ = ["CallGraph", "Edge", "EdgeKind", "LocationKey", "ReferenceEdge"]
