"""Call-graph structure: nodes, call edges, reference edges and subgraph derivation.

``call_graph`` holds the graph itself, ``edge`` the edge types, and ``location_key``
the physical-location identity used to dedup LSP qualified-name aliases.
"""

from static_analyzer.cfg.call_graph import CallGraph
from static_analyzer.cfg.edge import CallSiteLocation, DEFAULT_REFERENCE_KINDS, Edge, EdgeKind, ReferenceEdge

__all__ = ["DEFAULT_REFERENCE_KINDS", "CallGraph", "CallSiteLocation", "Edge", "EdgeKind", "ReferenceEdge"]
