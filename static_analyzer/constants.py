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
    RUST = "rust"
    CSHARP = "csharp"
    CPP = "cpp"


# File extensions per language. Every ``Language`` member appears here — keep
# it that way so adding a new language forces you to list its extensions in
# the same edit. ``mypy`` enforces total coverage via the assertion below.
LANGUAGE_EXTENSIONS: dict[Language, tuple[str, ...]] = {
    Language.PYTHON: (".py",),
    Language.TYPESCRIPT: (".ts", ".tsx", ".mts", ".cts"),
    Language.JAVASCRIPT: (".js", ".jsx", ".mjs", ".cjs"),
    Language.GO: (".go",),
    Language.JAVA: (".java",),
    Language.PHP: (".php",),
    Language.RUST: (".rs",),
    Language.CSHARP: (".cs",),
    Language.CPP: (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h"),
}

# Import-time invariant: every language has an extension list. Cheap check that
# catches drift when the enum grows without a matching ``LANGUAGE_EXTENSIONS`` entry.
assert set(LANGUAGE_EXTENSIONS) == set(
    Language
), f"LANGUAGE_EXTENSIONS missing: {set(Language) - set(LANGUAGE_EXTENSIONS)}"

# Flattened reverse lookup: extension -> language. Used by the diff boundary
# (``repo_utils/diff_parser.py``) to filter non-source changes and by
# ``static_analyzer.semantic_diff`` / ``diagram_analysis.incremental_tracer`` to
# route files to the right parser. Derived from ``LANGUAGE_EXTENSIONS`` so
# adding a language in one place updates both.
SOURCE_EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ext: language for language, exts in LANGUAGE_EXTENSIONS.items() for ext in exts
}


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

    # Display limits
    MAX_DISPLAY_CLUSTERS = 55  # Maximum clusters to show in output (readability limit)

    # Separator used by every ``LanguageAdapter.build_qualified_name``.
    # A future per-language switch (e.g. Rust to ``::``) would need both a
    # per-adapter override and updates to consumers that hardcode
    # ``.split(".")`` (``language_adapter.extract_package``,
    # ``cluster_methods_mixin.py``, ``diagnose_relations.py``).
    QUALIFIED_NAME_DELIMITER = "."

    # Deterministic seed for clustering algorithms
    CLUSTERING_SEED = 42


class NodeType(IntEnum):
    """LSP SymbolKind constants as an IntEnum.

    The integer values match the LSP specification so comparisons with raw LSP
    ``symbol.get("kind")`` still work transparently (IntEnum is an int subclass).

    All 26 standard LSP SymbolKind values are included so that any symbol kind
    returned by an LSP server can be represented without raising ValueError.
    """

    FILE = 1
    MODULE = 2
    NAMESPACE = 3
    PACKAGE = 4
    CLASS = 5
    METHOD = 6
    PROPERTY = 7
    FIELD = 8
    CONSTRUCTOR = 9
    ENUM = 10
    INTERFACE = 11
    FUNCTION = 12
    VARIABLE = 13
    CONSTANT = 14
    STRING = 15
    NUMBER = 16
    BOOLEAN = 17
    ARRAY = 18
    OBJECT = 19
    KEY = 20
    NULL = 21
    ENUM_MEMBER = 22
    STRUCT = 23
    EVENT = 24
    OPERATOR = 25
    TYPE_PARAMETER = 26

    def label(self) -> str:
        """Return a human-readable label (e.g. ``'Function'``, ``'Class'``)."""
        return ENTITY_LABELS.get(self, "Function")

    @classmethod
    def from_name(cls, name: str) -> "NodeType":
        """Construct from the enum member name (e.g. ``'METHOD'``).

        Also accepts old integer-string representations for backward compatibility
        (e.g. ``'6'`` -> ``NodeType.METHOD``).
        """
        try:
            return cls[name]
        except KeyError:
            return cls(int(name))

    # -- Convenience sets (defined after members via _ignore_) ----------------
    # IntEnum forbids non-member class attributes, so convenience sets are
    # defined as module-level constants below.


# Convenience sets – module-level so mypy can resolve them without monkey-patching.
CALLABLE_TYPES: set[NodeType] = {NodeType.METHOD, NodeType.FUNCTION, NodeType.CONSTRUCTOR}
CLASS_TYPES: set[NodeType] = {NodeType.CLASS, NodeType.INTERFACE, NodeType.STRUCT, NodeType.ENUM}
DATA_TYPES: set[NodeType] = {
    NodeType.PROPERTY,
    NodeType.FIELD,
    NodeType.VARIABLE,
    NodeType.CONSTANT,
    NodeType.ENUM_MEMBER,
}
GRAPH_NODE_TYPES: set[NodeType] = {
    NodeType.CLASS,
    NodeType.METHOD,
    NodeType.FUNCTION,
    NodeType.CONSTRUCTOR,
    NodeType.INTERFACE,
    NodeType.STRUCT,
    NodeType.ENUM,
}

ENTITY_LABELS: dict[NodeType, str] = {
    NodeType.FILE: "File",
    NodeType.MODULE: "Module",
    NodeType.NAMESPACE: "Namespace",
    NodeType.PACKAGE: "Package",
    NodeType.CLASS: "Class",
    NodeType.METHOD: "Method",
    NodeType.PROPERTY: "Property",
    NodeType.FIELD: "Field",
    NodeType.CONSTRUCTOR: "Constructor",
    NodeType.ENUM: "Enum",
    NodeType.INTERFACE: "Interface",
    NodeType.FUNCTION: "Function",
    NodeType.VARIABLE: "Variable",
    NodeType.CONSTANT: "Constant",
    NodeType.STRING: "String",
    NodeType.NUMBER: "Number",
    NodeType.BOOLEAN: "Boolean",
    NodeType.ARRAY: "Array",
    NodeType.OBJECT: "Object",
    NodeType.KEY: "Key",
    NodeType.NULL: "Null",
    NodeType.ENUM_MEMBER: "EnumMember",
    NodeType.STRUCT: "Struct",
    NodeType.EVENT: "Event",
    NodeType.OPERATOR: "Operator",
    NodeType.TYPE_PARAMETER: "TypeParameter",
}
