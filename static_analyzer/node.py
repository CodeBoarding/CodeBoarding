"""Node class for the static analyzer call graph.

Extracted from constants.py so that module contains only constants.
"""

from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES, DATA_TYPES, ENTITY_LABELS, NodeType


class Node:
    """Call-graph node for LSP SymbolKind. Use NodeType for type constants."""

    def __init__(
        self,
        fully_qualified_name: str,
        node_type: NodeType | int,
        file_path: str,
        line_start: int,
        line_end: int,
        col_start: int = 0,
    ) -> None:
        self.fully_qualified_name = fully_qualified_name
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.col_start = col_start
        self.type: NodeType = NodeType(node_type)
        self.methods_called_by_me: set[str] = set()

    def entity_label(self) -> str:
        """Return human-readable label based on LSP SymbolKind."""
        return ENTITY_LABELS.get(self.type, "Function")

    def is_callable(self) -> bool:
        """Return True if this node represents a callable entity (function or method)."""
        return self.type in CALLABLE_TYPES

    def is_class(self) -> bool:
        """Return True if this node represents a class."""
        return self.type in CLASS_TYPES

    def is_data(self) -> bool:
        """Return True if this node represents a data entity (property, field, variable, constant)."""
        return self.type in DATA_TYPES

    # Patterns indicating callback or anonymous function nodes from LSP
    _CALLBACK_PATTERNS = (") callback", "<function>", "<arrow")

    def is_callback_or_anonymous(self) -> bool:
        """Return True if this node represents a callback or anonymous function.

        LSP servers often report inline callbacks (e.g. `.forEach() callback`,
        `.find() callback`) and anonymous functions (e.g. `<function>`, `<arrow`)
        as separate symbols. These are typically not independently callable and
        should be excluded from certain health checks like unused code detection.
        """
        name = self.fully_qualified_name
        return any(pattern in name for pattern in self._CALLBACK_PATTERNS)

    def added_method_called_by_me(self, node: "Node") -> None:
        if isinstance(node, Node):
            self.methods_called_by_me.add(node.fully_qualified_name)
        else:
            raise ValueError("Expected a Node instance.")

    def __hash__(self) -> int:
        return hash(self.fully_qualified_name)

    def __repr__(self) -> str:
        return f"Node({self.fully_qualified_name}, {self.file_path}, {self.line_start}-{self.line_end})"
