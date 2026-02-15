"""Centralized constants for the static analyzer module.

This module contains all language and configuration constants used throughout
the static analyzer to avoid hardcoded strings and ensure consistency.
"""

from enum import StrEnum


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
    MIN_COVERAGE_RATIO = 0.75  # At least 75% of nodes should be in meaningful clusters
    MAX_SINGLETON_RATIO = 0.6  # No more than 60% singleton clusters (indicates poor clustering)
    MIN_CLUSTER_COUNT_RATIO = 6  # Minimum clusters = target_clusters // 6 (avoid too few clusters)
    MAX_CLUSTER_COUNT_MULTIPLIER = 2  # Maximum clusters = target_clusters * 2

    # Cluster size constraints
    SMALL_GRAPH_MAX_CLUSTER_RATIO = 0.6  # For graphs < 50 nodes, max cluster can be 60% of total
    LARGE_GRAPH_MAX_CLUSTER_RATIO = 0.4  # For larger graphs, max cluster should be 40% of total
    MAX_SIZE_TO_AVG_RATIO = 8  # Largest cluster shouldn't be more than 8x average size
    SMALL_GRAPH_THRESHOLD = 50  # Threshold between "small" and "large" graphs

    # Cluster balancing parameters
    MIN_CLUSTER_SIZE_MULTIPLIER = 3  # When merging, stop at min_size * 3 to avoid oversized clusters
    MAX_CLUSTER_SIZE_MULTIPLIER = 3  # Max cluster size = (total_nodes // target_clusters) * 3
    MIN_MAX_CLUSTER_SIZE = 10  # Absolute minimum for max cluster size

    # Display limits
    MAX_DISPLAY_CLUSTERS = 25  # Maximum clusters to show in output (readability limit)

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



class NodeType:
    # LSP SymbolKind constants
    CLASS_TYPE = 5
    METHOD_TYPE = 6
    PROPERTY_TYPE = 7
    FIELD_TYPE = 8
    FUNCTION_TYPE = 12
    VARIABLE_TYPE = 13
    CONSTANT_TYPE = 14

    # Sets for easy filtering
    CALLABLE_TYPES = {METHOD_TYPE, FUNCTION_TYPE}
    CLASS_TYPES = {CLASS_TYPE}
    DATA_TYPES = {PROPERTY_TYPE, FIELD_TYPE, VARIABLE_TYPE, CONSTANT_TYPE}

    ENTITY_LABELS = {
        CLASS_TYPE: "Class",
        METHOD_TYPE: "Method",
        PROPERTY_TYPE: "Property",
        FIELD_TYPE: "Field",
        FUNCTION_TYPE: "Function",
        VARIABLE_TYPE: "Variable",
        CONSTANT_TYPE: "Constant",
    }



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

