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
    MIN_COVERAGE_RATIO = 0.5  # At least 50% of nodes should be in meaningful clusters

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

    # Node types that should be included in the call graph and references
    GRAPH_NODE_TYPES = {CLASS_TYPE, METHOD_TYPE, FUNCTION_TYPE}

    ENTITY_LABELS = {
        CLASS_TYPE: "Class",
        METHOD_TYPE: "Method",
        PROPERTY_TYPE: "Property",
        FIELD_TYPE: "Field",
        FUNCTION_TYPE: "Function",
        VARIABLE_TYPE: "Variable",
        CONSTANT_TYPE: "Constant",
    }
