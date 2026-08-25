"""Centralized configuration for the static analyzer module.

This module contains all language and configuration constants used throughout
the static analyzer to avoid hardcoded strings and ensure consistency.
"""

from dataclasses import dataclass
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


class AdapterName(StrEnum):
    """Keys of ``ADAPTER_REGISTRY``. Not ``Language``: one adapter can own several."""

    PYTHON = "Python"
    TYPESCRIPT = "TypeScript"
    JAVASCRIPT = "JavaScript"
    CSHARP = "CSharp"
    GO = "Go"
    JAVA = "Java"
    PHP = "PHP"
    RUST = "Rust"


class SourceSuffix(StrEnum):
    """File suffixes we recognise, so a suffix is never a loose string."""

    PY = ".py"
    TS = ".ts"
    TSX = ".tsx"
    MTS = ".mts"
    CTS = ".cts"
    JS = ".js"
    JSX = ".jsx"
    MJS = ".mjs"
    CJS = ".cjs"
    GO = ".go"
    JAVA = ".java"
    PHP = ".php"
    RS = ".rs"
    CS = ".cs"
    CPP = ".cpp"
    CC = ".cc"
    CXX = ".cxx"
    HPP = ".hpp"
    HH = ".hh"
    HXX = ".hxx"
    H = ".h"


class JsxLanguageId(StrEnum):
    """The only LSP language ids with no ``Language`` of their own.

    Why: a ``.tsx`` is TypeScript, but tsserver derives its scriptKind from the id and
    reads JSX as a type assertion unless it gets the dialect.
    """

    TYPESCRIPT = "typescriptreact"
    JAVASCRIPT = "javascriptreact"


# File extensions per language. Every ``Language`` member appears here — keep
# it that way so adding a new language forces you to list its extensions in
# the same edit. ``mypy`` enforces total coverage via the assertion below.
LANGUAGE_EXTENSIONS: dict[Language, tuple[SourceSuffix, ...]] = {
    Language.PYTHON: (SourceSuffix.PY,),
    # TypeScript carries the JavaScript suffixes too: one tsserver serves the whole family,
    # and ``allowJs`` puts .js inside a TypeScript project.
    Language.TYPESCRIPT: (
        SourceSuffix.TS,
        SourceSuffix.TSX,
        SourceSuffix.MTS,
        SourceSuffix.CTS,
        SourceSuffix.JS,
        SourceSuffix.JSX,
        SourceSuffix.MJS,
        SourceSuffix.CJS,
    ),
    Language.JAVASCRIPT: (SourceSuffix.JS, SourceSuffix.JSX, SourceSuffix.MJS, SourceSuffix.CJS),
    Language.GO: (SourceSuffix.GO,),
    Language.JAVA: (SourceSuffix.JAVA,),
    Language.PHP: (SourceSuffix.PHP,),
    Language.RUST: (SourceSuffix.RS,),
    Language.CSHARP: (SourceSuffix.CS,),
    Language.CPP: (
        SourceSuffix.CPP,
        SourceSuffix.CC,
        SourceSuffix.CXX,
        SourceSuffix.HPP,
        SourceSuffix.HH,
        SourceSuffix.HXX,
        SourceSuffix.H,
    ),
}


# Import-time invariant: every language has an extension list. Cheap check that
# catches drift when the enum grows without a matching ``LANGUAGE_EXTENSIONS`` entry.
assert set(LANGUAGE_EXTENSIONS) == set(
    Language
), f"LANGUAGE_EXTENSIONS missing: {set(Language) - set(LANGUAGE_EXTENSIONS)}"

# Flattened reverse lookup: extension -> language, for filtering non-source
# changes. Derived from ``LANGUAGE_EXTENSIONS`` so adding a language in one
# place updates both.
SOURCE_EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ext: language for language, exts in LANGUAGE_EXTENSIONS.items() for ext in exts
}

# One server serves the whole TypeScript/JavaScript family, so its results live in one
# bucket. This says which member owns that bucket, so a read for either still finds it.
FAMILY_OWNER: dict[Language, Language] = {Language.JAVASCRIPT: Language.TYPESCRIPT}

# The languageId a document is opened with. Follows the file, not whichever adapter
# owns the family, so a .tsx is never opened as plain "typescript".
LANGUAGE_ID_BY_SUFFIX: dict[str, str] = {
    **{suffix: language.value for language, suffixes in LANGUAGE_EXTENSIONS.items() for suffix in suffixes},
    SourceSuffix.TSX: JsxLanguageId.TYPESCRIPT,
    SourceSuffix.JSX: JsxLanguageId.JAVASCRIPT,
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

    # Recursive hierarchy expansion thresholds
    MIN_METHODS_TO_EXPAND = 30
    MAX_LEAF_FILES = 12
    MAX_LEAF_METHODS = 120
    EXPAND_MODULARITY_THRESHOLD = 0.15

    # Separator used by every ``LanguageAdapter.build_qualified_name``.
    # A future per-language switch (e.g. Rust to ``::``) would need both a
    # per-adapter override and updates to consumers that hardcode
    # ``.split(".")`` (``language_adapter.extract_package``,
    # ``static_analysis_enricher_mixin.py``, ``diagnose_relations.py``).
    QUALIFIED_NAME_DELIMITER = "."

    # Deterministic seed for clustering algorithms
    CLUSTERING_SEED = 42


@dataclass(frozen=True)
class GroupingConfig:
    min_components: int
    max_components: int
    seed: int = ClusteringConfig.CLUSTERING_SEED
    drift_budget: float = 0.10
    resolutions: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0)


DEFAULT_GROUPING_CONFIG = GroupingConfig(min_components=5, max_components=8)
SUBCOMPONENT_GROUPING_CONFIG = GroupingConfig(min_components=3, max_components=8)


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

# Name fragments an LSP server uses for a symbol that is a real lexical scope but not a
# declaration a reader would name: inline callbacks (``map() callback``) and anonymous
# functions (``<function>``). A call written inside one belongs to whatever encloses it.
ANONYMOUS_SYMBOL_MARKERS: tuple[str, ...] = (") callback", "<function>", "<arrow", "<unknown>")
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
