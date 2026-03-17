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

    @property
    def definition_location(self) -> tuple[str, int, int]:
        """Return (uri, line, char) for deduplication."""
        return (str(self.file_path), self.start_line, self.start_char)


@dataclass
class Edge:
    """A directed edge in the call flow graph."""

    source: str
    destination: str


@dataclass
class CallFlowGraph:
    """Call flow graph with nodes and directed edges."""

    nodes: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    @classmethod
    def from_edge_set(cls, edge_set: set[tuple[str, str]]) -> CallFlowGraph:
        """Build a CFG from a set of (source, destination) tuples."""
        nodes_set: set[str] = set()
        edges = []
        for src, dst in sorted(edge_set):
            nodes_set.add(src)
            nodes_set.add(dst)
            edges.append(Edge(source=src, destination=dst))
        return cls(nodes=sorted(nodes_set), edges=edges)


@dataclass
class LanguageAnalysisResult:
    """Analysis result for a single language."""

    references: dict[str, dict] = field(default_factory=dict)
    hierarchy: dict[str, dict] = field(default_factory=dict)
    cfg: CallFlowGraph = field(default_factory=CallFlowGraph)
    package_dependencies: dict[str, dict] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)


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
