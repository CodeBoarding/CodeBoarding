"""Canonical typed program graph produced by static analysis.

The graph stores static-analysis entities and their directed relationships:

* CALL: caller -> callee
* CONTAINS: container -> member
* IMPORTS: importing file -> imported file/external package
* INHERITS: child -> parent

ProgramGraph is the source of truth for structural analysis and clustering.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable
import copy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

import networkx as nx

from static_analyzer.clustering import InfomapClusterSnapshot
from static_analyzer.clustering import ClusterResult
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES, DATA_TYPES, GRAPH_NODE_TYPES, NodeType
from static_analyzer.method_cluster_paths import MethodClusterPaths


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

    @property
    def id(self) -> str:
        return self.node_id

    def entity_label(self) -> str:
        return self.symbol_type.label() if self.symbol_type is not None else self.kind.value.replace("_", " ").title()

    def is_callable(self) -> bool:
        return self.symbol_type in CALLABLE_TYPES

    def is_class(self) -> bool:
        return self.symbol_type in CLASS_TYPES

    def is_data(self) -> bool:
        return self.symbol_type in DATA_TYPES

    def is_callback_or_anonymous(self) -> bool:
        return any(pattern in self.id for pattern in (") callback", "<function>", "<arrow"))


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

    def occurrence_dicts(self) -> list[dict[str, int | str]]:
        return [
            {"file": occurrence.file, "line": occurrence.line, "column": occurrence.column}
            for occurrence in self.occurrences
        ]

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
    cluster_snapshot: InfomapClusterSnapshot | None = field(default=None, repr=False, compare=False)
    method_cluster_paths: MethodClusterPaths = field(default_factory=MethodClusterPaths, repr=False, compare=False)
    _location_index: dict[tuple[str, int, int, int, int], str] = field(default_factory=dict, repr=False, compare=False)
    _aliases: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        initial_nodes = list(self.nodes.values())
        self.nodes = {}
        for node in sorted(initial_nodes, key=lambda item: item.id):
            self.add_node(node)

    @property
    def edges(self) -> list[ProgramEdge]:
        return [self._edges[key] for key in sorted(self._edges, key=lambda k: (k[0].value, k[1], k[2]))]

    def add_node(self, node: ProgramNode) -> None:
        if node.kind == ProgramNodeKind.SYMBOL and node.symbol_type is not None:
            location = self._symbol_location(node)
            existing_id = self._location_index.get(location)
            if existing_id is not None and existing_id != node.id:
                canonical_id = max((existing_id, node.id), key=lambda item: (len(item), item))
                if canonical_id == existing_id:
                    self._aliases[node.id] = existing_id
                    return
                self._promote_symbol(existing_id, node)
                return

        existing = self.nodes.get(node.node_id)
        if existing is None:
            self.nodes[node.node_id] = node
        elif existing.kind != node.kind:
            raise ValueError(f"Node ID collision for {node.node_id!r}: {existing.kind} vs {node.kind}")
        elif node.kind == ProgramNodeKind.SYMBOL and len(node.file_path) >= len(existing.file_path):
            self.nodes[node.node_id] = node
        if node.kind == ProgramNodeKind.SYMBOL and node.symbol_type is not None:
            self._location_index[self._symbol_location(node)] = node.id

    @staticmethod
    def _symbol_location(node: ProgramNode) -> tuple[str, int, int, int, int]:
        if node.symbol_type is None:
            raise ValueError(f"Program node {node.id!r} has no symbol type")
        return (node.file_path, node.line_start, node.line_end, node.symbol_type.value, node.col_start)

    def _promote_symbol(self, existing_id: str, canonical: ProgramNode) -> None:
        self.nodes.pop(existing_id)
        self.nodes[canonical.id] = canonical
        self._location_index[self._symbol_location(canonical)] = canonical.id
        for alias, target in list(self._aliases.items()):
            if target == existing_id:
                self._aliases[alias] = canonical.id
        self._aliases[existing_id] = canonical.id

        existing_edges = self.edges
        self._edges = {}
        for edge in existing_edges:
            if edge.source == existing_id:
                edge.source = canonical.id
            if edge.target == existing_id:
                edge.target = canonical.id
            self.add_edge(edge)

    def resolve_symbol_id(self, node_id: str) -> str:
        return self._aliases.get(node_id, node_id)

    def add_edge(self, edge: ProgramEdge) -> None:
        edge.source = self.resolve_symbol_id(edge.source)
        edge.target = self.resolve_symbol_id(edge.target)
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise ValueError(f"Program edge endpoints must exist: {edge.source!r} -> {edge.target!r}")
        key = (edge.kind, edge.source, edge.target)
        if key in self._edges:
            self._edges[key].merge(edge)
        else:
            edge.occurrences = sorted(set(edge.occurrences))
            self._edges[key] = edge

    def add_call(
        self,
        source: str,
        target: str,
        *,
        occurrences: Iterable[ProgramOccurrence] = (),
    ) -> None:
        """Add a directed call while preserving all known source occurrences."""
        self.add_edge(
            ProgramEdge(
                kind=ProgramEdgeKind.CALL,
                source=source,
                target=target,
                occurrences=list(occurrences),
            )
        )

    def edges_of_kind(self, kind: ProgramEdgeKind) -> list[ProgramEdge]:
        return [edge for edge in self.edges if edge.kind == kind]

    def nodes_of_kind(self, kind: ProgramNodeKind) -> list[ProgramNode]:
        return [self.nodes[node_id] for node_id in sorted(self.nodes) if self.nodes[node_id].kind == kind]

    def symbol_nodes(self, *, reference_worthy_only: bool = False) -> list[ProgramNode]:
        symbols = self.nodes_of_kind(ProgramNodeKind.SYMBOL)
        if reference_worthy_only:
            symbols = [node for node in symbols if node.reference_worthy]
        return symbols

    @property
    def symbols(self) -> dict[str, ProgramNode]:
        return {node.id: node for node in self.symbol_nodes()}

    def call_edges(self) -> list[ProgramEdge]:
        return self.edges_of_kind(ProgramEdgeKind.CALL)

    def call_node_ids(self) -> set[str]:
        endpoints = {node_id for edge in self.call_edges() for node_id in (edge.source, edge.target)}
        return {node.id for node in self.symbol_nodes() if node.symbol_type in GRAPH_NODE_TYPES or node.id in endpoints}

    def has_symbol(self, symbol_id: str) -> bool:
        symbol_id = self.resolve_symbol_id(symbol_id)
        return symbol_id in self.nodes and self.nodes[symbol_id].kind == ProgramNodeKind.SYMBOL

    def merge(self, other: "ProgramGraph") -> None:
        if self.language != other.language:
            raise ValueError(f"Cannot merge {self.language!r} graph with {other.language!r}")
        for node_id in sorted(other.nodes):
            self.add_node(other.nodes[node_id])
        for alias, target in sorted(other._aliases.items()):
            self._aliases[alias] = self.resolve_symbol_id(target)
        for edge in other.edges:
            self.add_edge(edge)
        self.method_cluster_paths.merge(other.method_cluster_paths)

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
            if src_name == dst_name:
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
        self._location_index = {
            self._symbol_location(node): node.id for node in self.symbol_nodes() if node.symbol_type is not None
        }
        if self.cluster_snapshot is not None:
            self.cluster_snapshot.node_paths = {
                id_map.get(node_id, node_id): path for node_id, path in self.cluster_snapshot.node_paths.items()
            }
            self.cluster_snapshot.module_members = {
                cluster_id: {id_map.get(node_id, node_id) for node_id in members}
                for cluster_id, members in self.cluster_snapshot.module_members.items()
            }
            result = self.cluster_snapshot.cluster_result
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
        out._aliases = {alias: target for alias, target in self._aliases.items() if target in out.nodes}
        out.method_cluster_paths = self.method_cluster_paths.prune(out.nodes)
        out.cluster_snapshot = copy.deepcopy(self.cluster_snapshot)
        if out.cluster_snapshot is not None:
            surviving = set(out.nodes)
            out.cluster_snapshot.node_paths = {
                node_id: path for node_id, path in out.cluster_snapshot.node_paths.items() if node_id in surviving
            }
            out.cluster_snapshot.module_members = {
                cluster_id: members & surviving
                for cluster_id, members in out.cluster_snapshot.module_members.items()
                if members & surviving
            }
        return out

    def induced_by_symbols(self, symbol_ids: Iterable[str]) -> "ProgramGraph":
        """Return a strict symbol scope plus its file/package containment context."""
        included = {self.resolve_symbol_id(node_id) for node_id in symbol_ids if self.has_symbol(node_id)}
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
        out._aliases = {alias: target for alias, target in self._aliases.items() if target in out.nodes}
        out.cluster_snapshot = copy.deepcopy(self.cluster_snapshot)
        out.method_cluster_paths = self.method_cluster_paths.prune(out.nodes)
        return out

    def filter_by_files(self, file_paths: set[str]) -> "ProgramGraph":
        return self.induced_by_symbols(node.id for node in self.symbol_nodes() if node.file_path in file_paths)

    def filter_by_nodes(self, symbol_ids: set[str]) -> "ProgramGraph":
        return self.induced_by_symbols(symbol_ids)

    def record_cluster_paths(self, cluster_result: ClusterResult, scope_id: str = "") -> None:
        self.method_cluster_paths.record(cluster_result, scope_id)

    def method_cluster_paths_snapshot(self) -> list[tuple[str, set[str]]]:
        return self.method_cluster_paths.snapshot()

    def to_networkx(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        for node in self.symbol_nodes():
            graph.add_node(
                node.id,
                file_path=node.file_path,
                line_start=node.line_start,
                line_end=node.line_end,
                type=node.symbol_type,
            )
        for edge in self.call_edges():
            graph.add_edge(edge.source, edge.target)
        return graph

    def to_cluster_string(
        self,
        cluster_result: ClusterResult,
        cluster_ids: Collection[int] = frozenset(),
        skip_nodes: Collection[str] = frozenset(),
    ) -> str:
        if not cluster_result.clusters:
            return cluster_result.strategy if cluster_result.strategy in {"empty", "none"} else "No clusters found."
        selected = sorted(cluster_ids or cluster_result.clusters)
        skip = set(skip_nodes)
        lines = [f"Cluster Definitions ({len(selected)} clusters):", ""]
        for cluster_id in selected:
            members = sorted(cluster_result.clusters.get(cluster_id, set()) - skip)
            if not members:
                continue
            lines.append(f"Cluster {cluster_id} ({len(members)} nodes):")
            by_file: defaultdict[str, list[str]] = defaultdict(list)
            for member in members:
                node = self.nodes.get(member)
                if node is not None:
                    by_file[node.file_path].append(f"{member} [{node.entity_label()}]")
            for file_path, labels in sorted(by_file.items()):
                lines.append(f"  {file_path}:")
                lines.extend(f"    {label}" for label in sorted(labels))
            lines.append("")
        owner = {
            member: cluster_id
            for cluster_id in selected
            for member in cluster_result.clusters.get(cluster_id, set()) - skip
        }
        cross = [
            (edge.source, edge.target)
            for edge in self.call_edges()
            if edge.source in owner and edge.target in owner and owner[edge.source] != owner[edge.target]
        ]
        lines.extend(["Inter-Cluster Connections:", ""])
        lines.extend(f"  - {source} -> {target}" for source, target in sorted(cross))
        if not cross:
            lines.append("No inter-cluster connections detected.")
        return "\n".join(lines) + "\n"

    def llm_str(self, size_limit: int = 2_500_000, skip_nodes: Collection[ProgramNode] = ()) -> str:
        skip = {node.id for node in skip_nodes}
        calls: defaultdict[str, list[str]] = defaultdict(list)
        for edge in self.call_edges():
            if edge.source not in skip and edge.target not in skip:
                calls[edge.source].append(edge.target)
        lines = [
            f"Control flow graph with {len(self.symbols) - len(skip)} nodes and {sum(map(len, calls.values()))} edges"
        ]
        for source, targets in sorted(calls.items()):
            node = self.nodes[source]
            lines.append(f"{node.entity_label()} {source} calls: {', '.join(sorted(targets))}")
        return ("\n".join(lines) + "\n")[:size_limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "aliases": dict(sorted(self._aliases.items())),
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
        graph._aliases = {str(alias): str(target) for alias, target in data.get("aliases", {}).items()}
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
