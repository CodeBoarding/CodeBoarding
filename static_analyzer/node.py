"""Node class for the static analyzer call graph.

Extracted from constants.py so that module contains only constants.
"""

from static_analyzer.constants import NodeType


class Node:
    """Call-graph node with type constants for LSP SymbolKind (also on NodeType in constants)."""

    # Mirror NodeType for code that references Node.CLASS_TYPE etc. (e.g. lsp_client, health tests)
    CLASS_TYPE = NodeType.CLASS_TYPE
    METHOD_TYPE = NodeType.METHOD_TYPE
    PROPERTY_TYPE = NodeType.PROPERTY_TYPE
    FIELD_TYPE = NodeType.FIELD_TYPE
    FUNCTION_TYPE = NodeType.FUNCTION_TYPE
    VARIABLE_TYPE = NodeType.VARIABLE_TYPE
    CONSTANT_TYPE = NodeType.CONSTANT_TYPE

    def __init__(
        self,
        fully_qualified_name: str,
        node_type: int,
        file_path: str,
        line_start: int,
        line_end: int,
    ) -> None:
        self.fully_qualified_name = fully_qualified_name
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.type = node_type
        self.methods_called_by_me: set[str] = set()

    def entity_label(self) -> str:
        """Return human-readable label based on LSP SymbolKind."""
        return NodeType.ENTITY_LABELS.get(self.type, "Function")

    def is_callable(self) -> bool:
        """Return True if this node represents a callable entity (function or method)."""
        return self.type in NodeType.CALLABLE_TYPES

    def is_class(self) -> bool:
        """Return True if this node represents a class."""
        return self.type in NodeType.CLASS_TYPES

    def is_data(self) -> bool:
        """Return True if this node represents a data entity (property, field, variable, constant)."""
        return self.type in NodeType.DATA_TYPES

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
