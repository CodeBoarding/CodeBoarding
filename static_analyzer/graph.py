from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

import networkx as nx

from static_analyzer.constants import ClusteringConfig, NodeType
from static_analyzer.method_cluster_paths import MethodClusterPaths
from static_analyzer.node import Node

logger = logging.getLogger(__name__)

_EMPTY_NODES: Mapping[str, Node] = MappingProxyType({})


class EdgeKind(StrEnum):
    """Kind of relationship an edge represents.

    ``CALL`` edges live in ``CallGraph.edges`` and drive component *relations*.
    The rest are *reference edges* (``CallGraph.reference_edges``): structural
    relationships the pure call graph misses — a method belongs to its class
    (CONTAINS), a class extends another (INHERITS), code names a type (TYPEREF),
    a module imports another (IMPORT). They complete the graph for *clustering*
    (so constructors/dunders/DI/interface methods aren't graph-isolated) without
    polluting the call-relation semantics.
    """

    CALL = "call"
    CONTAINS = "contains"
    INHERITS = "inherits"
    TYPEREF = "typeref"
    IMPORT = "import"


@dataclass(frozen=True)
class LocationKey:
    """Hashable key identifying a symbol's physical location in the source tree."""

    file_path: str
    line_start: int
    line_end: int
    node_type: int
    col_start: int = 0


# Defined here rather than in ``clustering.models`` (its API home, which re-exports
# it): CallGraph's pickled cluster cache references this class, so hosting it next
# to CallGraph avoids a ``graph <-> clustering.models`` import cycle and keeps
# ``static_analyzer.graph.ClusterResult`` resolvable for older pickled baselines.
@dataclass
class ClusterResult:
    """Result of clustering a CallGraph. Provides deterministic cluster IDs and file mappings."""

    clusters: dict[int, set[str]] = field(default_factory=dict)  # cluster_id -> node names
    cluster_to_files: dict[int, set[str]] = field(default_factory=dict)  # cluster_id -> file_paths
    file_to_clusters: dict[str, set[int]] = field(default_factory=dict)  # file_path -> cluster_ids
    strategy: str = ""  # which algorithm was used

    def get_cluster_ids(self) -> set[int]:
        return set(self.clusters.keys())

    def get_files_for_cluster(self, cluster_id: int) -> set[str]:
        return self.cluster_to_files.get(cluster_id, set())

    def get_clusters_for_file(self, file_path: str) -> set[int]:
        return self.file_to_clusters.get(file_path, set())

    def get_nodes_for_cluster(self, cluster_id: int) -> set[str]:
        return self.clusters.get(cluster_id, set())

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.cluster_to_files = {cid: {fn(path) for path in paths} for cid, paths in self.cluster_to_files.items()}
        remapped_file_to_clusters: dict[str, set[int]] = defaultdict(set)
        for path, cluster_ids in self.file_to_clusters.items():
            remapped_file_to_clusters[fn(path)].update(cluster_ids)
        self.file_to_clusters = dict(remapped_file_to_clusters)

    def pruned_to(self, surviving_files: dict[str, str]) -> "ClusterResult":
        """Copy holding only the qnames in ``surviving_files`` (qname -> file_path), file maps recomputed."""
        pruned_clusters: dict[int, set[str]] = {}
        pruned_cluster_to_files: dict[int, set[str]] = {}
        pruned_file_to_clusters: dict[str, set[int]] = {}
        for cid, members in self.clusters.items():
            kept = {m for m in members if m in surviving_files}
            if not kept:
                continue
            pruned_clusters[cid] = kept
            files: set[str] = set()
            for qname in kept:
                fp = surviving_files[qname]
                if fp:
                    files.add(fp)
                    pruned_file_to_clusters.setdefault(fp, set()).add(cid)
            if files:
                pruned_cluster_to_files[cid] = files
        return ClusterResult(
            clusters=pruned_clusters,
            cluster_to_files=pruned_cluster_to_files,
            file_to_clusters=pruned_file_to_clusters,
            strategy=self.strategy,
        )


class Edge:
    def __init__(self, src_node: Node, dst_node: Node, call_sites: Sequence[Mapping[str, Hashable]] = ()) -> None:
        self.src_node = src_node
        self.dst_node = dst_node
        self._call_sites: list[dict[str, Hashable]] = []
        self._call_site_keys: set[tuple[tuple[str, Hashable], ...]] = set()
        for site in call_sites:
            self.add_call_site(site)

    @property
    def call_sites(self) -> list[dict[str, Hashable]]:
        return [dict(site) for site in self._call_sites]

    def get_source(self) -> str:
        return self.src_node.fully_qualified_name

    def get_destination(self) -> str:
        return self.dst_node.fully_qualified_name

    def __repr__(self) -> str:
        return f"Edge({self.src_node.fully_qualified_name} -> {self.dst_node.fully_qualified_name})"

    def add_call_site(self, call_site: Mapping[str, Hashable]) -> None:
        call_site = self._normalize_call_site(call_site)
        call_site_key = tuple(sorted(call_site.items()))
        if call_site_key not in self._call_site_keys:
            self._call_site_keys.add(call_site_key)
            self._call_sites.append(call_site)

    @staticmethod
    def _normalize_call_site(call_site: Mapping[str, Hashable]) -> dict[str, Hashable]:
        normalized = dict(call_site)
        if "file" not in normalized and "file_path" in normalized:
            normalized["file"] = normalized.pop("file_path")
        return normalized

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        for site in self._call_sites:
            if "file" in site:
                site["file"] = fn(str(site["file"]))
        self._call_site_keys = {tuple(sorted(site.items())) for site in self._call_sites}


class CallGraph:
    def __init__(
        self,
        nodes: Mapping[str, Node] = _EMPTY_NODES,
        edges: Sequence[Edge] = (),
        language: str = "python",
    ) -> None:
        self.nodes = dict(nodes)
        self.edges = list(edges)
        self._edge_by_key: dict[tuple[str, str], Edge] = {}
        for edge in self.edges:
            edge_key = (edge.get_source(), edge.get_destination())
            self._edge_by_key[edge_key] = edge
        self.language = language.lower()
        # Every adapter currently emits ``.``-separated qualified names; see
        # ``constants.QUALIFIED_NAME_DELIMITER`` for the language-switch caveat.
        self.delimiter = ClusteringConfig.QUALIFIED_NAME_DELIMITER
        # Cache for cluster result
        self._cluster_cache: ClusterResult | None = None
        # qname -> scoped cluster ids it belongs to, e.g. ["1", "1.3", "1.3.6"].
        self.method_cluster_paths = MethodClusterPaths()
        # Location-based dedup: (file_path, line_start, line_end, type) -> canonical qualified name.
        # When the LSP produces multiple qualified-name aliases for the same
        # physical symbol (e.g. ``src.index.funcA`` vs
        # ``container.agent-runner.src.index.funcA``), only the most specific
        # (longest) name is kept.  The shorter alias is recorded here so that
        # ``add_edge`` can transparently resolve references to dropped aliases.
        self._location_index: dict[LocationKey, str] = {}
        self._alias_to_canonical: dict[str, str] = {}
        # Non-call relationship edges (CONTAINS/INHERITS/TYPEREF/IMPORT), kept off
        # ``self.edges`` so relations and ``methods_called_by_me`` stay call-only.
        # Merged into the graph only for clustering (``clustering_networkx``).
        # Each entry: (src_qname, dst_qname, EdgeKind value).
        self.reference_edges: list[tuple[str, str, str]] = []

    def add_node(self, node: Node) -> None:
        loc_key = LocationKey(node.file_path, node.line_start, node.line_end, node.type.value, node.col_start)
        existing_name = self._location_index.get(loc_key)

        if existing_name is not None:
            if len(node.fully_qualified_name) > len(existing_name):
                # New name is more specific — promote the existing node in-place
                # so that Edge objects referencing it automatically see the new name.
                canonical = node.fully_qualified_name
                old_node = self.nodes.pop(existing_name)
                old_node.fully_qualified_name = canonical
                self.nodes[canonical] = old_node
                self._location_index[loc_key] = canonical
                # Flatten alias chain: repoint any alias that targeted the old name
                for alias, target in self._alias_to_canonical.items():
                    if target == existing_name:
                        self._alias_to_canonical[alias] = canonical
                self._alias_to_canonical[existing_name] = canonical
                for s, d in self._edge_by_key:
                    new_s = canonical if s == existing_name else s
                    new_d = canonical if d == existing_name else d
                    # Update methods_called_by_me on source nodes
                    if d == existing_name and new_s in self.nodes:
                        src_node = self.nodes[new_s]
                        src_node.methods_called_by_me.discard(existing_name)
                        src_node.methods_called_by_me.add(canonical)
                self._edge_by_key = {(edge.get_source(), edge.get_destination()): edge for edge in self.edges}
            else:
                # Existing name is already the most specific — record alias.
                self._alias_to_canonical[node.fully_qualified_name] = existing_name
            return

        if node.fully_qualified_name not in self.nodes:
            self.nodes[node.fully_qualified_name] = node
            self._location_index[loc_key] = node.fully_qualified_name

    def has_node(self, name: str) -> bool:
        """Check if a name (or any of its aliases) maps to a node in the graph."""
        return self._resolve_name(name) in self.nodes

    def _resolve_name(self, name: str) -> str:
        """Resolve a possibly-aliased name to the canonical name in the graph."""
        return self._alias_to_canonical.get(name, name)

    def add_edge(self, src_name: str, dst_name: str, call_sites: Sequence[Mapping[str, Hashable]] = ()) -> None:
        src_name = self._resolve_name(src_name)
        dst_name = self._resolve_name(dst_name)

        if src_name not in self.nodes or dst_name not in self.nodes:
            raise ValueError("Both source and destination nodes must exist in the graph.")

        edge_key = (src_name, dst_name)
        if edge_key in self._edge_by_key:
            for call_site in call_sites:
                self._edge_by_key[edge_key].add_call_site(dict(call_site))
            return

        edge = Edge(self.nodes[src_name], self.nodes[dst_name], [])
        for call_site in call_sites:
            edge.add_call_site(dict(call_site))
        self.edges.append(edge)
        self._edge_by_key[edge_key] = edge

        self.nodes[src_name].added_method_called_by_me(self.nodes[dst_name])

    def add_reference_edge(self, src_name: str, dst_name: str, kind: EdgeKind) -> None:
        """Record a non-call relationship edge (CONTAINS/INHERITS/TYPEREF/IMPORT).

        Stored separately from call edges; used only to complete the graph for
        clustering. Silently ignores endpoints that aren't nodes or self-loops.
        """
        src_name = self._resolve_name(src_name)
        dst_name = self._resolve_name(dst_name)
        if src_name in self.nodes and dst_name in self.nodes and src_name != dst_name:
            self.reference_edges.append((src_name, dst_name, str(kind)))

    def _carry_reference_edges(self, out: "CallGraph", *extra_sources: "CallGraph") -> None:
        """Copy reference edges whose both endpoints survive into a derived graph.

        Includes ``self`` and any ``extra_sources`` (e.g. the ``other`` side of a union), so
        reference edges freshly computed for changed/added files are not dropped when both
        endpoints survive. Deduped, keeping only edges whose endpoints are both in ``out``.
        """
        seen: set[tuple[str, str, str]] = set()
        carried: list[tuple[str, str, str]] = []
        for source in (self, *extra_sources):
            for s, d, k in getattr(source, "reference_edges", ()):
                # Resolve through the SOURCE's alias map: an endpoint stored under a short
                # alias must map to the canonical name ``out`` promoted it to, or a call edge
                # (which add_edge resolves) survives while its reference edge is silently dropped.
                rs, rd = source._resolve_name(s), source._resolve_name(d)
                if rs in out.nodes and rd in out.nodes and rs != rd and (rs, rd, k) not in seen:
                    seen.add((rs, rd, k))
                    carried.append((rs, rd, k))
        out.reference_edges = carried

    def filter(
        self,
        keep_node: Callable[[Node], bool],
        on_dropped_edge: Callable[[Edge], None],
    ) -> "CallGraph":
        """Return a new CallGraph keeping only nodes matching ``keep_node`` and connecting edges.

        ``_cluster_cache`` is preserved and pruned to the surviving qnames so
        a warm-start invalidation/filter step doesn't silently drop the prior
        clustering. Edges whose endpoints both survive are re-added; edges
        with a dropped endpoint are cascaded out and optionally collected.
        """
        out = CallGraph(language=self.language)
        for node in self.nodes.values():
            if keep_node(node):
                out.add_node(node)
        for edge in self.edges:
            src, dst = edge.get_source(), edge.get_destination()
            if out.has_node(src) and out.has_node(dst):
                try:
                    out.add_edge(src, dst, call_sites=edge.call_sites)
                except ValueError as e:
                    logger.warning(f"Failed to add edge {src} -> {dst} during filter: {e}")
            else:
                on_dropped_edge(edge)
        out._cluster_cache = self._prune_cluster_cache(out.nodes)
        out.method_cluster_paths = self._prune_method_cluster_paths(out.nodes)
        self._carry_reference_edges(out)
        return out

    def union(self, other: "CallGraph") -> "CallGraph":
        """Return a new CallGraph unioning ``self`` (cached) with ``other`` (fresh).

        ``_cluster_cache`` comes from ``self`` (the cached side that was
        clustered in a prior run), pruned to the merged-node set. ``other``'s
        nodes are new and unclustered until the next clustering pass; that's
        the intended cluster_delta input — new files appear unassigned.
        """
        out = CallGraph(language=self.language)
        for node in self.nodes.values():
            out.add_node(node)
        for node in other.nodes.values():
            out.add_node(node)
        for edge in self.edges:
            try:
                out.add_edge(edge.get_source(), edge.get_destination(), call_sites=edge.call_sites)
            except ValueError:
                pass
        for edge in other.edges:
            try:
                out.add_edge(edge.get_source(), edge.get_destination(), call_sites=edge.call_sites)
            except ValueError:
                pass
        out._cluster_cache = self._prune_cluster_cache(out.nodes)
        out.method_cluster_paths = self._prune_method_cluster_paths(out.nodes)
        # Carry reference edges from BOTH sides: ``other`` holds the fresh reference edges for
        # changed/added files, which would otherwise be lost and revert those files to call-only.
        self._carry_reference_edges(out, other)
        return out

    def _prune_cluster_cache(self, surviving_nodes: dict[str, Node]) -> "ClusterResult | None":
        """Drop qnames not in ``surviving_nodes`` from ``_cluster_cache``; recompute file maps."""
        if self._cluster_cache is None:
            return None
        return self._cluster_cache.pruned_to({qname: node.file_path for qname, node in surviving_nodes.items()})

    def _prune_method_cluster_paths(self, surviving_nodes: dict[str, Node]) -> MethodClusterPaths:
        return self.method_cluster_paths.prune(surviving_nodes)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        for node in self.nodes.values():
            node.file_path = fn(node.file_path)
        for edge in self.edges:
            edge.visit_paths(fn)
        if self._cluster_cache is not None:
            self._cluster_cache.visit_paths(fn)

    def record_cluster_paths(self, cluster_result: ClusterResult, scope_id: str = "") -> None:
        """Record each member's current cluster id for this scope."""
        self.method_cluster_paths.record(cluster_result, scope_id)

    @property
    def cluster_cache(self) -> ClusterResult | None:
        """The leaf clustering last computed for this graph, pruned on filtering."""
        return self._cluster_cache

    def set_cluster_cache(self, cluster_result: ClusterResult) -> None:
        self._cluster_cache = cluster_result

    def method_cluster_paths_snapshot(self) -> list[tuple[str, set[str]]]:
        return self.method_cluster_paths.snapshot()

    def to_networkx(self) -> nx.DiGraph:
        nx_graph = nx.DiGraph()
        for node in self.nodes.values():
            nx_graph.add_node(
                node.fully_qualified_name,
                file_path=node.file_path,
                line_start=node.line_start,
                line_end=node.line_end,
                type=node.type,
            )
        for edge in self.edges:
            nx_graph.add_edge(edge.get_source(), edge.get_destination())
        return nx_graph

    def to_networkx_with_references(self, reference_kinds: Collection[str] | None = None) -> nx.DiGraph:
        """Graph used for clustering: call edges plus configured reference-edge kinds.

        Reference edges (CONTAINS/INHERITS/TYPEREF/IMPORT) complete the graph so
        constructors, dunders, DI/reflection-invoked, and interface methods aren't
        graph-isolated. ``reference_kinds`` defaults to
        ``ClusteringConfig.CLUSTERING_EDGE_KINDS``; pass an explicit set to analyze
        a different subset. Call edges are always included.
        """
        kinds = set(ClusteringConfig.CLUSTERING_EDGE_KINDS if reference_kinds is None else reference_kinds)
        nx_graph = self.to_networkx()
        # getattr: baselines pickled before reference edges existed lack the attribute.
        # Resolve endpoints through the alias map so an edge stored under a short alias still
        # lands on the canonical node (matching how add_edge resolves call edges).
        for src, dst, kind in getattr(self, "reference_edges", ()):
            rsrc, rdst = self._resolve_name(src), self._resolve_name(dst)
            if kind in kinds and rsrc in self.nodes and rdst in self.nodes:
                nx_graph.add_edge(rsrc, rdst)
        return nx_graph

    def filter_by_files(self, file_paths: set[str]) -> "CallGraph":
        """
        Create a new CallGraph containing only nodes from the specified files.
        Only includes edges where both source and target nodes are in the specified files.
        """
        relevant_nodes = {node_id: node for node_id, node in self.nodes.items() if node.file_path in file_paths}

        # Filter edges: both source and target must be in relevant_nodes
        relevant_edges = []
        for edge in self.edges:
            source_name = edge.get_source()
            target_name = edge.get_destination()

            if self.nodes[source_name].file_path in file_paths and self.nodes[target_name].file_path in file_paths:
                relevant_edges.append(edge)

        filtered_edges = []
        for edge in relevant_edges:
            src = edge.get_source()
            dst = edge.get_destination()
            filtered_edges.append(Edge(self.nodes[src], self.nodes[dst], [dict(site) for site in edge.call_sites]))

        # Create new graph, preserving the source language
        sub_graph = CallGraph(nodes=relevant_nodes, edges=filtered_edges, language=self.language)
        sub_graph.method_cluster_paths = self._prune_method_cluster_paths(relevant_nodes)
        self._carry_reference_edges(sub_graph)

        return sub_graph

    def filter_by_nodes(self, qualified_names: set[str]) -> "CallGraph":
        """Create a new CallGraph containing only the specified nodes (by qualified name).

        Only includes edges where both source and target are in the allowed set.
        """
        relevant_nodes = {nid: node for nid, node in self.nodes.items() if nid in qualified_names}

        filtered_edges = []
        for edge in self.edges:
            if edge.get_source() in relevant_nodes and edge.get_destination() in relevant_nodes:
                filtered_edges.append(
                    Edge(
                        self.nodes[edge.get_source()],
                        self.nodes[edge.get_destination()],
                        [dict(site) for site in edge.call_sites],
                    )
                )

        sub_graph = CallGraph(nodes=relevant_nodes, edges=filtered_edges, language=self.language)
        sub_graph.method_cluster_paths = self._prune_method_cluster_paths(relevant_nodes)
        self._carry_reference_edges(sub_graph)
        return sub_graph

    @staticmethod
    def _common_dot_prefix(qualified_names: list[str]) -> str:
        """Longest dotted-segment prefix shared by all qualified names, leaving at least one trailing segment each."""
        if len(qualified_names) < 2:
            return ""
        parts_list = [n.split(".") for n in qualified_names]
        min_len = min(len(p) for p in parts_list)
        common: list[str] = []
        for i in range(min_len - 1):
            seg = parts_list[0][i]
            if all(p[i] == seg for p in parts_list):
                common.append(seg)
            else:
                break
        return ".".join(common)

    def __str__(self) -> str:
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges\n"
        for _, node in self.nodes.items():
            if node.methods_called_by_me:
                result += f"Method {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}\n"
        return result

    def llm_str(self, size_limit: int = 2_500_000, skip_nodes: Sequence[Node] = ()) -> str:
        skip_set = set(skip_nodes)

        # Level 1: Full method-level detail (default __str__ but with file grouping)
        default_str = self._llm_str_detailed(skip_set)

        logger.info(f"[CFG Tool] LLM string: {len(default_str)} characters, size limit: {size_limit} characters")

        if len(default_str) <= size_limit:
            return default_str

        # Level 2: Class-level with top method edges preserved
        logger.info(
            f"[CallGraph] Control flow graph is too large ({len(default_str)} chars), switching to class-level summary."
        )
        class_str = self._llm_str_class_level(skip_set)

        logger.info(f"[CallGraph] Class-level summary: {len(class_str)} characters")
        return class_str

    def _llm_str_detailed(self, skip_set: set[Node]) -> str:
        """Level 1: File-grouped, method-level detail with call targets."""
        # Group nodes by file
        file_nodes: dict[str, list[Node]] = defaultdict(list)
        for node in self.nodes.values():
            if node not in skip_set:
                file_nodes[node.file_path].append(node)

        active_nodes = sum(len(v) for v in file_nodes.values())
        active_edges = sum(
            1
            for e in self.edges
            if self.nodes[e.get_source()] not in skip_set and self.nodes[e.get_destination()] not in skip_set
        )

        result = f"Control flow graph with {active_nodes} nodes and {active_edges} edges\n"

        for file_path in sorted(file_nodes):
            nodes = sorted(file_nodes[file_path], key=lambda n: n.fully_qualified_name)
            for node in nodes:
                if node.methods_called_by_me:
                    label = node.entity_label()
                    targets = ", ".join(sorted(node.methods_called_by_me))
                    result += f"{label} {node.fully_qualified_name} calls: {targets}\n"

        return result

    def _llm_str_class_level(self, skip_set: set[Node]) -> str:
        """Level 2: Class-to-class summary with call counts and top edges."""
        class_calls: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        function_calls: list[str] = []

        for node in self.nodes.values():
            if node in skip_set or not node.methods_called_by_me:
                continue

            parts = node.fully_qualified_name.split(self.delimiter)
            if node.type == NodeType.METHOD and len(parts) > 1:
                class_name = self.delimiter.join(parts[:-1])
                method_short = parts[-1]

                for called_method in node.methods_called_by_me:
                    called_parts = called_method.split(self.delimiter)
                    if len(called_parts) > 1:
                        called_class = self.delimiter.join(called_parts[:-1])
                        called_short = called_parts[-1]
                        class_calls[class_name][called_class].append(f"{method_short}->{called_short}")
                    else:
                        class_calls[class_name][called_method].append(f"{method_short}->{called_method}")
            else:
                targets = ", ".join(sorted(node.methods_called_by_me))
                function_calls.append(f"Function {node.fully_qualified_name} calls: {targets}")

        active_count = sum(1 for n in self.nodes.values() if n not in skip_set)
        result = f"Control flow graph with {active_count} nodes (class-level summary)\n"

        for class_name in sorted(class_calls):
            called_targets = class_calls[class_name]
            target_strs = []
            for target_class in sorted(called_targets):
                edges = called_targets[target_class]
                count = len(edges)
                # Show up to 3 representative method pairs
                examples = ", ".join(edges[:3])
                suffix = f" +{count - 3} more" if count > 3 else ""
                target_strs.append(f"{target_class} ({count} calls: {examples}{suffix})")
            result += f"Class {class_name} -> {'; '.join(target_strs)}\n"

        for func_call in function_calls:
            result += func_call + "\n"

        logger.info(f"[CallGraph] Class-level summary: {len(result)} characters")
        return result
