"""Centralized constants for the static analyzer module.

This module contains all language and configuration constants used throughout
the static analyzer to avoid hardcoded strings and ensure consistency.
"""

from enum import IntEnum, StrEnum


class Language(StrEnum):
    """Enumeration of supported programming languages.

    Using Enum ensures type safety and prevents typos in language names.
    The values are the lowercase language identifiers used in LSP and throughout
    the codebase.
    """

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    GO = "go"
    JAVA = "java"
    PHP = "php"
    CPP = "cpp"


class ClusteringConfig:
    """Configuration constants for graph clustering algorithms.

    These values are based on empirical testing with codebases ranging from
    100-10,000 nodes. They balance clustering quality with computational efficiency.
    """

    # Default clustering parameters - chosen to work well for typical codebases (500-2000 nodes)
    DEFAULT_TARGET_CLUSTERS = 20  # Sweet spot for human comprehension and LLM context
    DEFAULT_MIN_CLUSTER_SIZE = 2  # Avoid singleton clusters that don't show relationships

    # Quality thresholds for determining "good" clustering
    MIN_COVERAGE_RATIO = 0.75  # At least 50% of nodes should be in meaningful clusters

    # Display limits
    MAX_DISPLAY_CLUSTERS = 55  # Maximum clusters to show in output (readability limit)

    # Language-specific delimiters for qualified names
    DEFAULT_DELIMITER = "."  # Works for Python, Java, C#
    DELIMITER_MAP = {
        Language.PYTHON: ".",
        Language.GO: ".",
        Language.PHP: "\\",  # PHP uses backslash for namespaces
        Language.TYPESCRIPT: ".",
        Language.JAVASCRIPT: ".",
        Language.JAVA: ".",
    }

    # Deterministic seed for clustering algorithms
    CLUSTERING_SEED = 42


class NodeType(IntEnum):
    """LSP SymbolKind constants as an IntEnum.

    The integer values match the LSP specification so comparisons with raw LSP
    ``symbol.get("kind")`` still work transparently (IntEnum is an int subclass).
    """

    CLASS = 5
    METHOD = 6
    PROPERTY = 7
    FIELD = 8
    FUNCTION = 12
    VARIABLE = 13
    CONSTANT = 14

    def label(self) -> str:
        """Return a human-readable label (e.g. ``'Function'``, ``'Class'``)."""
        return _ENTITY_LABELS.get(self, "Function")

    @classmethod
    def from_name(cls, name: str) -> "NodeType":
        """Construct from the enum member name (e.g. ``'METHOD'``).

        Also accepts old integer-string representations for backward compatibility
        (e.g. ``'6'`` → ``NodeType.METHOD``).
        """
        try:
            return cls[name]
        except KeyError:
            return cls(int(name))

    # -- Convenience sets (defined after members via _ignore_) ----------------
    # IntEnum forbids non-member class attributes, so we attach these after
    # class creation below.


# Attach convenience sets and labels as plain class attributes so they don't
# become enum members.
NodeType.CALLABLE_TYPES = {NodeType.METHOD, NodeType.FUNCTION}  # type: ignore[attr-defined]
NodeType.CLASS_TYPES = {NodeType.CLASS}  # type: ignore[attr-defined]
NodeType.DATA_TYPES = {NodeType.PROPERTY, NodeType.FIELD, NodeType.VARIABLE, NodeType.CONSTANT}  # type: ignore[attr-defined]
NodeType.GRAPH_NODE_TYPES = {NodeType.CLASS, NodeType.METHOD, NodeType.FUNCTION}  # type: ignore[attr-defined]

_ENTITY_LABELS: dict[NodeType, str] = {
    NodeType.CLASS: "Class",
    NodeType.METHOD: "Method",
    NodeType.PROPERTY: "Property",
    NodeType.FIELD: "Field",
    NodeType.FUNCTION: "Function",
    NodeType.VARIABLE: "Variable",
    NodeType.CONSTANT: "Constant",
}
NodeType.ENTITY_LABELS = _ENTITY_LABELS  # type: ignore[attr-defined]
