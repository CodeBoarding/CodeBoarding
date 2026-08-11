"""Output-only labels for persisted LSP node types."""

_NODE_TYPE_NAMES = (
    "",
    "FILE",
    "MODULE",
    "NAMESPACE",
    "PACKAGE",
    "CLASS",
    "METHOD",
    "PROPERTY",
    "FIELD",
    "CONSTRUCTOR",
    "ENUM",
    "INTERFACE",
    "FUNCTION",
    "VARIABLE",
    "CONSTANT",
    "STRING",
    "NUMBER",
    "BOOLEAN",
    "ARRAY",
    "OBJECT",
    "KEY",
    "NULL",
    "ENUM_MEMBER",
    "STRUCT",
    "EVENT",
    "OPERATOR",
    "TYPE_PARAMETER",
)


def node_type_label(node_type: str) -> str:
    """Return the display label for a persisted LSP node type."""
    try:
        name = _NODE_TYPE_NAMES[int(node_type)] if node_type.isdigit() else node_type
    except IndexError as exc:
        raise ValueError(f"Unknown node type: {node_type}") from exc
    if not name or name not in _NODE_TYPE_NAMES:
        raise ValueError(f"Unknown node type: {node_type}")
    return name.title().replace("_", "")
