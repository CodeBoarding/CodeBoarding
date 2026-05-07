"""Per-language static-analysis buckets used by ``StaticAnalysisResults``.

Each bucket owns its own merge logic and exposes ``visit_paths(fn)`` so the
cache can rewrite file paths between absolute and repo-relative form without
reaching into bucket internals. Inner data defaults to ``None`` (rather than
empty containers) so callers can distinguish "never populated" (raises
``ValueError``) from "populated but empty" (returns the empty value).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


@dataclass
class ControlFlowGraph:
    graph: CallGraph | None = None

    def merge(self, other: CallGraph) -> None:
        if self.graph is None:
            self.graph = other
            return
        for node in other.nodes.values():
            self.graph.add_node(node)
        for edge in other.edges:
            try:
                self.graph.add_edge(edge.get_source(), edge.get_destination())
            except ValueError:
                pass

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        if self.graph is None:
            return
        for node in self.graph.nodes.values():
            node.file_path = fn(node.file_path)


@dataclass
class ClassHierarchy:
    entries: dict[str, dict] | None = None

    def merge(self, other: dict | list) -> None:
        if self.entries is None:
            self.entries = {}
        if isinstance(other, dict):
            self.entries.update(other)
        elif isinstance(other, list):
            for item in other:
                if isinstance(item, dict):
                    self.entries.update(item)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        if self.entries is None:
            return
        for info in self.entries.values():
            if isinstance(info, dict) and "file_path" in info:
                info["file_path"] = fn(info["file_path"])


@dataclass
class References:
    by_qualified_name: dict[str, Node] | None = None

    def add(self, refs: list[Node]) -> None:
        if self.by_qualified_name is None:
            self.by_qualified_name = {}
        for r in refs:
            self.by_qualified_name[r.fully_qualified_name] = r

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        if self.by_qualified_name is None:
            return
        for ref in self.by_qualified_name.values():
            if isinstance(ref, Node):
                ref.file_path = fn(ref.file_path)


@dataclass
class PackageDependencies:
    entries: dict[str, dict] | None = None

    def merge(self, other: dict) -> None:
        if self.entries is None:
            self.entries = {}
        if isinstance(other, dict):
            self.entries.update(other)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        if self.entries is None:
            return
        for pkg_info in self.entries.values():
            if isinstance(pkg_info, dict) and "files" in pkg_info:
                pkg_info["files"] = [fn(f) for f in pkg_info["files"]]


@dataclass
class SourceFiles:
    paths: list[str] | None = None

    def extend(self, files: list[str]) -> None:
        if self.paths is None:
            self.paths = []
        self.paths.extend(files)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        if self.paths is None:
            return
        self.paths = [fn(f) for f in self.paths]


@dataclass
class LanguageResults:
    """All static-analysis artefacts for a single language."""

    cfg: ControlFlowGraph = field(default_factory=ControlFlowGraph)
    hierarchy: ClassHierarchy = field(default_factory=ClassHierarchy)
    references: References = field(default_factory=References)
    dependencies: PackageDependencies = field(default_factory=PackageDependencies)
    source_files: SourceFiles = field(default_factory=SourceFiles)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.cfg.visit_paths(fn)
        self.hierarchy.visit_paths(fn)
        self.references.visit_paths(fn)
        self.dependencies.visit_paths(fn)
        self.source_files.visit_paths(fn)
