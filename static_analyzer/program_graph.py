"""Canonical typed program graph produced by static analysis.

The graph deliberately keeps semantic edge direction separate from the weighted
projection used by clustering:

* CALL: caller -> callee
* CONTAINS: container -> member
* IMPORTS: importing file -> imported file/external package
* INHERITS: child -> parent

CallGraph remains a projection for agent tooling; ProgramGraph is the source of
truth for structural analysis and clustering.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
import copy
from dataclasses import dataclass, field
from enum import StrEnum
from math import log1p
from pathlib import Path, PurePosixPath
from typing import Any

import networkx as nx

from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


class ProgramNodeKind(StrEnum):
    SYMBOL = "symbol"
    FILE = "file"
    PACKAGE = "package"
    EXTERNAL_PACKAGE = "external_package"


class ProgramEdgeKind(StrEnum):
    CALL = "call"
    CONTAINS = "contains"
    IMPORTS = "imports"
    INHERITS = "inherits"


@dataclass(frozen=True, order=True)
class ProgramOccurrence:
    file: str
    line: int
    column: int

    def visit_path(self, fn: Callable[[str], str]) -> "ProgramOccurrence":
        return ProgramOccurrence(file=fn(self.file), line=self.line, column=self.column)


@dataclass
class ProgramNode:
    node_id: str
    kind: ProgramNodeKind
    language: str
    name: str
    file_path: str = ""
    symbol_type: NodeType | None = None
    line_start: int = 0
    line_end: int = 0
    col_start: int = 0
    reference_worthy: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_legacy_node(self) -> Node:
        if self.kind != ProgramNodeKind.SYMBOL or self.symbol_type is None:
            raise ValueError(f"Program node {self.node_id!r} is not a source symbol")
        return Node(
            fully_qualified_name=self.node_id,
            node_type=self.symbol_type,
            file_path=self.file_path,
            line_start=self.line_start,
            line_end=self.line_end,
            col_start=self.col_start,
        )


@dataclass
class ProgramEdge:
    kind: ProgramEdgeKind
    source: str
    target: str
    occurrences: list[ProgramOccurrence] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def occurrence_count(self) -> int:
        return len(self.occurrences)

    def merge(self, other: "ProgramEdge") -> None:
        if (self.kind, self.source, self.target) != (other.kind, other.source, other.target):
            raise ValueError("Cannot merge different program edges")
        self.occurrences = sorted(set(self.occurrences) | set(other.occurrences))
        if self.kind == ProgramEdgeKind.IMPORTS:
            modules = set(self.metadata.get("declared_modules", []))
            modules.update(other.metadata.get("declared_modules", []))
            for metadata in (self.metadata, other.metadata):
                module = metadata.get("declared_module")
                if module:
                    modules.add(module)
            if modules:
                self.metadata["declared_modules"] = sorted(modules)
                self.metadata["declared_module"] = min(modules)
            for key, value in other.metadata.items():
                if key not in {"declared_module", "declared_modules"}:
                    self.metadata[key] = value
        else:
            self.metadata.update(other.metadata)


@dataclass
class ProgramGraph:
    language: str
    nodes: dict[str, ProgramNode] = field(default_factory=dict)
    _edges: dict[tuple[ProgramEdgeKind, str, str], ProgramEdge] = field(default_factory=dict)
    _cluster_snapshot: Any | None = field(default=None, repr=False, compare=False)
    _call_projection: CallGraph | None = field(default=None, repr=False, compare=False)

    @property
    def edges(self) -> list[ProgramEdge]:
        return [self._edges[key] for key in sorted(self._edges, key=lambda k: (k[0].value, k[1], k[2]))]

    def add_node(self, node: ProgramNode) -> None:
        existing = self.nodes.get(node.node_id)
        if existing is None:
            self.nodes[node.node_id] = node
        elif existing.kind != node.kind:
            raise ValueError(f"Node ID collision for {node.node_id!r}: {existing.kind} vs {node.kind}")
        elif node.kind == ProgramNodeKind.SYMBOL and len(node.file_path) >= len(existing.file_path):
            self.nodes[node.node_id] = node
        self._call_projection = None

    def add_edge(self, edge: ProgramEdge) -> None:
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise ValueError(f"Program edge endpoints must exist: {edge.source!r} -> {edge.target!r}")
        key = (edge.kind, edge.source, edge.target)
        if key in self._edges:
            self._edges[key].merge(edge)
        else:
            edge.occurrences = sorted(set(edge.occurrences))
            self._edges[key] = edge
        self._call_projection = None

    def edges_of_kind(self, kind: ProgramEdgeKind) -> list[ProgramEdge]:
        return [edge for edge in self.edges if edge.kind == kind]

    def nodes_of_kind(self, kind: ProgramNodeKind) -> list[ProgramNode]:
        return [self.nodes[node_id] for node_id in sorted(self.nodes) if self.nodes[node_id].kind == kind]

    def symbol_nodes(self, *, reference_worthy_only: bool = False) -> list[ProgramNode]:
        symbols = self.nodes_of_kind(ProgramNodeKind.SYMBOL)
        if reference_worthy_only:
            symbols = [node for node in symbols if node.reference_worthy]
        return symbols

    def merge(self, other: "ProgramGraph") -> None:
        if self.language != other.language:
            raise ValueError(f"Cannot merge {self.language!r} graph with {other.language!r}")
        for node_id in sorted(other.nodes):
            self.add_node(other.nodes[node_id])
        for edge in other.edges:
            self.add_edge(edge)

    def to_call_graph(self) -> CallGraph:
        if self._call_projection is not None:
            return self._call_projection
        call_graph = CallGraph(language=self.language)
        for node in self.symbol_nodes():
            call_graph.add_node(node.to_legacy_node())
        for edge in self.edges_of_kind(ProgramEdgeKind.CALL):
            if not call_graph.has_node(edge.source) or not call_graph.has_node(edge.target):
                continue
            call_graph.add_edge(
                edge.source,
                edge.target,
                call_sites=[
                    {"file": occurrence.file, "line": occurrence.line, "column": occurrence.column}
                    for occurrence in edge.occurrences
                ],
            )
        self._call_projection = call_graph
        return call_graph

    def hierarchy(self) -> dict[str, dict[str, Any]]:
        hierarchy: dict[str, dict[str, Any]] = {
            node.node_id: {
                "superclasses": [],
                "subclasses": [],
                "file_path": node.file_path,
                "line_start": node.line_start,
                "line_end": node.line_end,
            }
            for node in self.symbol_nodes()
            if node.symbol_type in {NodeType.CLASS, NodeType.INTERFACE, NodeType.STRUCT, NodeType.ENUM}
        }
        for edge in self.edges_of_kind(ProgramEdgeKind.INHERITS):
            hierarchy[edge.source]["superclasses"].append(edge.target)
            hierarchy[edge.target]["subclasses"].append(edge.source)
        for info in hierarchy.values():
            info["superclasses"].sort()
            info["subclasses"].sort()
        return hierarchy

    def package_dependencies(self) -> dict[str, dict[str, list[str]]]:
        """Return declared package dependencies derived from IMPORTS edges."""
        packages = {node.node_id: node for node in self.nodes_of_kind(ProgramNodeKind.PACKAGE)}
        file_to_package: dict[str, str] = {}
        for edge in self.edges_of_kind(ProgramEdgeKind.CONTAINS):
            if edge.source in packages and self.nodes[edge.target].kind == ProgramNodeKind.FILE:
                file_to_package[edge.target] = edge.source
        result: dict[str, dict[str, list[str]]] = {
            node.name: {"imports": [], "imported_by": []} for node in packages.values()
        }
        for edge in self.edges_of_kind(ProgramEdgeKind.IMPORTS):
            src_package_id = file_to_package.get(edge.source)
            target_node = self.nodes[edge.target]
            dst_package_id = file_to_package.get(edge.target)
            if src_package_id is None:
                continue
            src_name = packages[src_package_id].name
            if dst_package_id is not None:
                dst_name = packages[dst_package_id].name
            elif target_node.kind == ProgramNodeKind.EXTERNAL_PACKAGE:
                # Retain external-package nodes and IMPORTS edges in the graph,
                # but do not expose them through today's package/component view.
                continue
            else:
                continue
            if dst_name not in result[src_name]["imports"]:
                result[src_name]["imports"].append(dst_name)
            if dst_package_id is not None:
                result.setdefault(dst_name, {"imports": [], "imported_by": []})
                if src_name not in result[dst_name]["imported_by"]:
                    result[dst_name]["imported_by"].append(src_name)
        for info in result.values():
            info["imports"].sort()
            info["imported_by"].sort()
        return result

    def clustering_graph(
        self,
        *,
        call_weight: float = 1.0,
        containment_weight: float = 0.35,
        import_weight: float = 0.55,
        inheritance_weight: float = 0.35,
    ) -> nx.DiGraph:
        """Build the deterministic directed weighted graph consumed by Infomap."""
        graph = nx.DiGraph()
        for node_id in sorted(self.nodes):
            node = self.nodes[node_id]
            if node.kind == ProgramNodeKind.EXTERNAL_PACKAGE:
                continue
            graph.add_node(node_id, kind=node.kind.value, file_path=node.file_path)

        weights: defaultdict[tuple[str, str], float] = defaultdict(float)
        for edge in self.edges:
            if edge.source not in graph or edge.target not in graph:
                continue
            if edge.kind == ProgramEdgeKind.CALL:
                weights[(edge.source, edge.target)] += call_weight * log1p(edge.occurrence_count)
            elif edge.kind == ProgramEdgeKind.CONTAINS:
                weights[(edge.source, edge.target)] += containment_weight
                weights[(edge.target, edge.source)] += containment_weight
            elif edge.kind == ProgramEdgeKind.IMPORTS:
                weights[(edge.source, edge.target)] += import_weight
            elif edge.kind == ProgramEdgeKind.INHERITS:
                weights[(edge.source, edge.target)] += inheritance_weight

        for (source, target), weight in sorted(weights.items()):
            if weight > 0:
                graph.add_edge(source, target, weight=round(weight, 12))
        return graph

    def cluster(self) -> ClusterResult:
        from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer

        snapshot = HierarchicalInfomapClusterer().cluster(self, previous=self._cluster_snapshot)
        self._cluster_snapshot = snapshot
        return snapshot.cluster_result

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        """Rewrite every path and path-derived file node ID."""
        id_map: dict[str, str] = {}
        new_nodes: dict[str, ProgramNode] = {}
        for old_id in sorted(self.nodes):
            node = self.nodes[old_id]
            new_file_path = fn(node.file_path) if node.file_path else ""
            new_id = file_node_id(new_file_path) if node.kind == ProgramNodeKind.FILE else old_id
            id_map[old_id] = new_id
            node.node_id = new_id
            node.file_path = new_file_path
            if node.kind == ProgramNodeKind.FILE:
                node.name = new_file_path
            new_nodes[new_id] = node
        new_edges: dict[tuple[ProgramEdgeKind, str, str], ProgramEdge] = {}
        for edge in self.edges:
            edge.source = id_map[edge.source]
            edge.target = id_map[edge.target]
            edge.occurrences = [occurrence.visit_path(fn) for occurrence in edge.occurrences]
            new_edges[(edge.kind, edge.source, edge.target)] = edge
        self.nodes = new_nodes
        self._edges = new_edges
        self._call_projection = None
        if self._cluster_snapshot is not None:
            self._cluster_snapshot.node_paths = {
                id_map.get(node_id, node_id): path for node_id, path in self._cluster_snapshot.node_paths.items()
            }
            self._cluster_snapshot.module_members = {
                cluster_id: {id_map.get(node_id, node_id) for node_id in members}
                for cluster_id, members in self._cluster_snapshot.module_members.items()
            }
            result = self._cluster_snapshot.cluster_result
            result.cluster_to_files = {
                cluster_id: {fn(path) for path in paths} for cluster_id, paths in result.cluster_to_files.items()
            }
            result.file_to_clusters = {
                fn(path): set(cluster_ids) for path, cluster_ids in result.file_to_clusters.items()
            }

    def without_files(self, file_paths: Iterable[str]) -> "ProgramGraph":
        removed = set(file_paths)
        removed_node_ids = {
            node_id
            for node_id, node in self.nodes.items()
            if node.file_path in removed or (node.kind == ProgramNodeKind.FILE and node.name in removed)
        }
        out = ProgramGraph(language=self.language)
        for node_id in sorted(self.nodes):
            if node_id not in removed_node_ids:
                out.add_node(copy.deepcopy(self.nodes[node_id]))
        for edge in self.edges:
            if edge.source in out.nodes and edge.target in out.nodes:
                out.add_edge(copy.deepcopy(edge))
        return out

    def induced_by_symbols(self, symbol_ids: Iterable[str]) -> "ProgramGraph":
        """Return a strict symbol scope plus its file/package containment context."""
        included = {
            node_id
            for node_id in symbol_ids
            if node_id in self.nodes and self.nodes[node_id].kind == ProgramNodeKind.SYMBOL
        }
        changed = True
        while changed:
            changed = False
            for edge in self.edges_of_kind(ProgramEdgeKind.CONTAINS):
                if edge.target in included and edge.source not in included:
                    included.add(edge.source)
                    changed = True

        # External dependencies remain evidence on the scoped graph, while
        # internal imports outside the strict symbol scope stay excluded.
        for edge in self.edges_of_kind(ProgramEdgeKind.IMPORTS):
            if edge.source in included and self.nodes[edge.target].kind == ProgramNodeKind.EXTERNAL_PACKAGE:
                included.add(edge.target)

        out = ProgramGraph(language=self.language)
        for node_id in sorted(included):
            out.add_node(copy.deepcopy(self.nodes[node_id]))
        for edge in self.edges:
            if edge.source in included and edge.target in included:
                out.add_edge(copy.deepcopy(edge))
        out._cluster_snapshot = copy.deepcopy(self._cluster_snapshot)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "nodes": [
                {
                    "id": node.node_id,
                    "kind": node.kind.value,
                    "name": node.name,
                    "file": node.file_path,
                    "symbol_type": node.symbol_type.name if node.symbol_type is not None else None,
                    "line_start": node.line_start,
                    "line_end": node.line_end,
                    "col_start": node.col_start,
                    "reference_worthy": node.reference_worthy,
                    "metadata": node.metadata,
                }
                for node in (self.nodes[node_id] for node_id in sorted(self.nodes))
            ],
            "edges": [
                {
                    "kind": edge.kind.value,
                    "source": edge.source,
                    "target": edge.target,
                    "occurrences": [
                        {"file": occurrence.file, "line": occurrence.line, "column": occurrence.column}
                        for occurrence in edge.occurrences
                    ],
                    "metadata": edge.metadata,
                }
                for edge in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProgramGraph":
        graph = cls(language=str(data["language"]))
        for raw in data.get("nodes", []):
            symbol_type = raw.get("symbol_type")
            graph.add_node(
                ProgramNode(
                    node_id=str(raw["id"]),
                    kind=ProgramNodeKind(raw["kind"]),
                    language=str(data["language"]),
                    name=str(raw.get("name", raw["id"])),
                    file_path=str(raw.get("file", "")),
                    symbol_type=NodeType.from_name(symbol_type) if symbol_type else None,
                    line_start=int(raw.get("line_start", 0)),
                    line_end=int(raw.get("line_end", 0)),
                    col_start=int(raw.get("col_start", 0)),
                    reference_worthy=bool(raw.get("reference_worthy", False)),
                    metadata=dict(raw.get("metadata", {})),
                )
            )
        for raw in data.get("edges", []):
            graph.add_edge(
                ProgramEdge(
                    kind=ProgramEdgeKind(raw["kind"]),
                    source=str(raw["source"]),
                    target=str(raw["target"]),
                    occurrences=[
                        ProgramOccurrence(
                            file=str(item["file"]),
                            line=int(item["line"]),
                            column=int(item["column"]),
                        )
                        for item in raw.get("occurrences", [])
                    ],
                    metadata=dict(raw.get("metadata", {})),
                )
            )
        return graph


def file_node_id(file_path: str) -> str:
    return f"file::{PurePosixPath(file_path).as_posix()}"


def package_node_id(language: str, package: str) -> str:
    return f"package::{language.lower()}::{package}"


def external_package_node_id(language: str, package: str) -> str:
    return f"external::{language.lower()}::{package}"


def package_parent(package: str) -> str | None:
    parent = str(PurePosixPath(package.replace(".", "/")).parent).replace("/", ".")
    return None if parent in ("", ".") else parent


def normalized_path(path: str) -> str:
    return PurePosixPath(Path(path).as_posix()).as_posix()
