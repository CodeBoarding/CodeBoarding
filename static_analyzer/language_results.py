"""Per-language static-analysis buckets used by ``StaticAnalysisResults``.

Each bucket owns its own merge logic and exposes ``visit_paths(fn)`` so the
cache can rewrite file paths between absolute and repo-relative form without
reaching into bucket internals. Inner data defaults to ``None`` (rather than
empty containers) so callers can distinguish "never populated" (raises
``ValueError``) from "populated but empty" (returns the empty value).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import logging

from static_analyzer.graph import CallGraph, EdgeKind
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


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
            if not edge.call_sites:
                logger.warning(
                    "Merging CFG edge without call-site metadata: %s -> %s",
                    edge.get_source(),
                    edge.get_destination(),
                )
                self.graph.add_edge(edge.get_source(), edge.get_destination())
                continue
            try:
                self.graph.add_edge(
                    edge.get_source(),
                    edge.get_destination(),
                    call_sites=[dict(site) for site in edge.call_sites],
                )
            except (TypeError, ValueError):
                logger.warning(
                    "Merging CFG edge with invalid call-site metadata: %s -> %s",
                    edge.get_source(),
                    edge.get_destination(),
                    exc_info=True,
                )
                self.graph.add_edge(edge.get_source(), edge.get_destination())
        # Carry the other graph's reference edges (CONTAINS/INHERITS/TYPEREF/IMPORT) so a
        # same-language sub-project's completed graph isn't reduced to call-only after merge.
        # Re-added via the API so alias-resolution and node-existence guards apply post-merge.
        for src, dst, kind in other.reference_edges:
            self.graph.add_reference_edge(src, dst, EdgeKind(kind))
        self.graph.method_cluster_paths.merge(other.method_cluster_paths)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        if self.graph is None:
            return
        self.graph.visit_paths(fn)


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
