"""LSP SymbolKind groupings and constants for the engine modules.

``NodeType`` from ``static_analyzer.config`` is the single source of truth
for LSP SymbolKind integer values.  This module re-exports it for convenience
and defines derived groupings (CLASS_LIKE_KINDS, CALLABLE_KINDS).
"""

from static_analyzer.config import NodeType

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

# A type check can name a constructor function; a variant pattern names an enum member.
RUNTIME_REFERENCE_KINDS = CLASS_LIKE_KINDS | CALLABLE_KINDS | {NodeType.ENUM_MEMBER}

# Callables a call can dispatch to an override of. A constructor runs exactly the class it names.
DISPATCHED_KINDS: set[int] = {
    NodeType.FUNCTION,
    NodeType.METHOD,
}

# Batch size for did_open to avoid overwhelming LSP servers
DID_OPEN_BATCH_SIZE = 50

# Position requests sent per round trip: a long list does not wait on one timeout.
REQUEST_BATCH_SIZE = 50

# Share of RAM (or of a container's limit) the language servers up at once may hold. Why this much:
# a one-shot run keeps one server up at a time, and the rest is room for Python and the system.
MEMORY_BUDGET_FRACTION = 0.75
# The budget when memory is unknown.
MIN_MEMORY_BUDGET = 2 * 1024**3
MAX_MEMORY_BUDGET = 12 * 1024**3
# Smallest useful share when several servers are resident: below this a server
# recycles faster than it can index.
MIN_ENGINE_MEMORY_BUDGET = 1024**3

MEMORY_BUDGET_ENV_VAR = "CODEBOARDING_LSP_MEMORY_BUDGET_MB"
# What a Node-based server holds outside its heap, so the heap is its share less this.
SERVER_NON_HEAP_BYTES = 512 * 1024**2
