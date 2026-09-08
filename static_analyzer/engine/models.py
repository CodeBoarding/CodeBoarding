"""Data models for static analysis results.

Note: EdgeBuildContext was moved to edge_build_context.py to isolate
the circular type dependency between LSPClient, SymbolTable, and LanguageAdapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SymbolInfo:
    """Information about a discovered symbol."""

    name: str
    qualified_name: str
    kind: int
    file_path: Path
    start_line: int
    start_char: int
    end_line: int
    end_char: int
    parent_chain: list[tuple[str, int]] = field(default_factory=list)
    # Promoted to CLASS for having callable children: a namespace object, or a
    # `const raw = await fn(() => ...)` wrapper that is a scope rather than a name.
    promoted_from_variable: bool = False
    # Empty for a top-level symbol and for every alias.
    # Why: slicing a qualified name finds the real owner only by luck of the naming scheme.
    owner_qualified_name: str = ""

    @property
    def definition_location(self) -> tuple[str, int, int]:
        """Return (uri, line, char) for deduplication."""
        return (str(self.file_path), self.start_line, self.start_char)


@dataclass
class CallSite:
    """A physical source location where an edge is invoked."""

    file: str
    line: int
    column: int

    @classmethod
    def from_lsp_position(cls, file: str, line: int, column: int) -> "CallSite":
        return cls(file=file, line=line + 1, column=column + 1)

    @property
    def human_line(self) -> int:
        return self.line

    @property
    def human_column(self) -> int:
        return self.column

    @property
    def lsp_line(self) -> int:
        return self.line - 1

    @property
    def lsp_column(self) -> int:
        return self.column - 1


@dataclass(frozen=True)
class TypeReferenceSite:
    """A position naming a type without calling it: a parameter, base, generic argument, ``typeof``."""

    file: str
    line: int  # one-based, like ``CallSite``
    column: int
    name: str
    # The dotted prefix written before the name (``Volo.Abp`` in ``Volo.Abp.Foo``), else empty.
    qualifier: str = ""


@dataclass(frozen=True)
class UsingDirective:
    """One C# ``using`` and the lines it governs."""

    target: str
    # The enclosing scope's range: C# requires usings to precede declarations, so every site
    # that can see this directive is inside it.
    first_line: int
    last_line: int
    # The name an alias binds (``Item`` in ``using Item = A.B.Item``), else empty.
    alias: str = ""
    # ``using static N.T``: T's members become nameable unqualified, T's own name does not.
    static: bool = False


@dataclass(frozen=True)
class NamespaceContext:
    """What a C# file can name unqualified: its usings and the namespaces it declares."""

    usings: tuple[UsingDirective, ...]
    # (dotted namespace, first line, last line), one-based inclusive; nested names are joined.
    namespaces: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class ImportBinding:
    """One local name a TS/JS file imports: the module specifier and the exported name."""

    source: str
    imported_name: str


@dataclass
class Edge:
    """A directed edge in the call flow graph."""

    source: str
    destination: str
    call_sites: list[CallSite] = field(default_factory=list)


@dataclass
class CallFlowGraph:
    """Call flow graph with nodes and directed edges."""

    nodes: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    @classmethod
    def from_edge_set(cls, edge_set: dict[tuple[str, str], list[CallSite]]) -> CallFlowGraph:
        """Build a CFG from edge tuples with call-site metadata."""
        nodes_set: set[str] = set()
        edges = []
        for (src, dst), call_sites in sorted(edge_set.items()):
            nodes_set.add(src)
            nodes_set.add(dst)
            edges.append(Edge(source=src, destination=dst, call_sites=list(call_sites)))
        return cls(nodes=sorted(nodes_set), edges=edges)


@dataclass
class LanguageAnalysisResult:
    """Analysis result for a single language."""

    references: dict[str, dict] = field(default_factory=dict)
    hierarchy: dict[str, dict] = field(default_factory=dict)
    cfg: CallFlowGraph = field(default_factory=CallFlowGraph)
    package_dependencies: dict[str, dict] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)
    # Non-call relationship edges completing the graph for clustering. Each entry
    # is (source_qname, target_qname). type_references: code names a type (param,
    # return, annotation, cast); import_edges: module A imports symbol/module B.
    # TYPEREF is normally derived after the per-config graphs merge (see
    # ``type_reference_builder``); an engine may still pre-fill either list.
    type_references: list[tuple[str, str]] = field(default_factory=list)
    import_edges: list[tuple[str, str]] = field(default_factory=list)


class AnalysisResults:
    """Container for all analysis results, keyed by language."""

    def __init__(self) -> None:
        self._lang_results: dict[str, LanguageAnalysisResult] = {}

    def add_language_result(self, language: str, result: LanguageAnalysisResult) -> None:
        self._lang_results[language] = result

    def get_languages(self) -> set[str]:
        return set(self._lang_results.keys())

    def get_hierarchy(self, language: str) -> dict[str, dict]:
        if language not in self._lang_results:
            raise ValueError(f"No results for language: {language}")
        return self._lang_results[language].hierarchy

    def get_cfg(self, language: str) -> CallFlowGraph:
        if language not in self._lang_results:
            raise ValueError(f"No results for language: {language}")
        return self._lang_results[language].cfg

    def get_package_dependencies(self, language: str) -> dict[str, dict]:
        if language not in self._lang_results:
            raise ValueError(f"No results for language: {language}")
        return self._lang_results[language].package_dependencies

    def get_source_files(self, language: str) -> list[str]:
        if language not in self._lang_results:
            raise ValueError(f"No results for language: {language}")
        return self._lang_results[language].source_files
