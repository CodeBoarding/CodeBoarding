"""Call-graph structure: nodes, call edges, reference edges and subgraph derivation.

``call_graph`` holds the graph itself, ``edge`` the edge types, and ``location_key``
the physical-location identity used to dedup LSP qualified-name aliases.
"""

from static_analyzer.cfg.call_graph import CallGraph
from static_analyzer.cfg.edge import Edge, EdgeKind
from static_analyzer.cfg.location_key import LocationKey

__all__ = ["CallGraph", "Edge", "EdgeKind", "LocationKey"]
