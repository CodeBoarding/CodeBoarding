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

# Share of RAM the language server may hold before it gets recycled. This
# leaves room for Python, other language servers, and the operating system.
MEMORY_BUDGET_FRACTION = 0.4
MIN_MEMORY_BUDGET = 2 * 1024**3
MAX_MEMORY_BUDGET = 12 * 1024**3

MEMORY_BUDGET_ENV_VAR = "CODEBOARDING_LSP_MEMORY_BUDGET_MB"

# How long a batch keeps waiting after the server stops answering. The batch
# timeout still caps the total wait; this caps the tail. Sized well above a
# healthy batch's whole round-trip (~3s for 50 queries on a 4k-file C#
# solution) so only a genuinely stuck query trips it.
STRAGGLER_GRACE_SEC = 15.0


class EdgeStrategy(StrEnum):
    """Edge-building strategy selection for Phase 2."""

    REFERENCES = "references"
    DEFINITIONS = "definitions"
