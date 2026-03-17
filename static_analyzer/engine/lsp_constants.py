"""LSP SymbolKind groupings and constants for the engine modules.

``NodeType`` from ``static_analyzer.constants`` is the single source of truth
for LSP SymbolKind integer values.  This module re-exports it for convenience
and defines derived groupings (CLASS_LIKE_KINDS, CALLABLE_KINDS).
"""

from enum import StrEnum

from static_analyzer.constants import NodeType

CLASS_LIKE_KINDS: set[int] = {
    NodeType.CLASS,
    NodeType.INTERFACE,
    NodeType.STRUCT,
    NodeType.ENUM,
}

CALLABLE_KINDS: set[int] = {
    NodeType.FUNCTION,
    NodeType.METHOD,
    NodeType.CONSTRUCTOR,
}

# Batch size for did_open to avoid overwhelming LSP servers
DID_OPEN_BATCH_SIZE = 50


class EdgeStrategy(StrEnum):
    """Edge-building strategy selection for Phase 2."""

    REFERENCES = "references"
    DEFINITIONS = "definitions"
