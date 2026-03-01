import logging
from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx
import networkx.algorithms.community as nx_comm

from static_analyzer.constants import ClusteringConfig, NodeType, Language
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


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


class Edge:
    def __init__(self, src_node: Node, dst_node: Node) -> None:
        self.src_node = src_node
        self.dst_node = dst_node

    def get_source(self) -> str:
        return self.src_node.fully_qualified_name

    def get_destination(self) -> str:
        return self.dst_node.fully_qualified_name

    def __repr__(self) -> str:
        return f"Edge({self.src_node.fully_qualified_name} -> {self.dst_node.fully_qualified_name})"


class CallGraph:
    def __init__(
        self,
        nodes: dict[str, Node] | None = None,
        edges: list[Edge] | None = None,
        language: str = "python",
    ) -> None:
        self.nodes = nodes if nodes is not None else {}
        self.edges = edges if edges is not None else []
        self._edge_set: set[tuple[str, str]] = set()
        self.language = language.lower()
        # Set delimiter based on language for qualified name parsing
        # Convert string language to Language enum for lookup using list comprehension
        lang_key: Language | None = next((lang for lang in Language if lang.value == self.language), None)
        if lang_key and lang_key in ClusteringConfig.DELIMITER_MAP:
            self.delimiter = ClusteringConfig.DELIMITER_MAP[lang_key]
        else:
            self.delimiter = ClusteringConfig.DEFAULT_DELIMITER
        # Cache for cluster result
        self._cluster_cache: ClusterResult | None = None

    def add_node(self, node: Node) -> None:
        if node.type not in NodeType.GRAPH_NODE_TYPES:
            return
        if node.fully_qualified_name not in self.nodes:
            self.nodes[node.fully_qualified_name] = node

    def add_edge(self, src_name: str, dst_name: str) -> None:
        if src_name not in self.nodes or dst_name not in self.nodes:
            raise ValueError("Both source and destination nodes must exist in the graph.")

        edge_key = (src_name, dst_name)
        if edge_key in self._edge_set:
            return

        edge = Edge(self.nodes[src_name], self.nodes[dst_name])
        self.edges.append(edge)
        self._edge_set.add(edge_key)

        self.nodes[src_name].added_method_called_by_me(self.nodes[dst_name])

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

    def cluster(
        self,
        target_clusters: int = ClusteringConfig.DEFAULT_TARGET_CLUSTERS,
        min_cluster_size: int = ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE,
    ) -> ClusterResult:
        """Cluster the graph using a try-all-then-level-up approach.

        Flow: try all algorithms at each abstraction level (None, class, file).
        If coverage >= 50% at any level, stop and return the best result.
        Falls back to connected components if everything fails.
        """
        if self._cluster_cache is not None:
            return self._cluster_cache

        nx_graph = self.to_networkx()
        if nx_graph.number_of_nodes() == 0:
            logger.warning("No nodes available for clustering.")
            self._cluster_cache = ClusterResult(strategy="empty")
            return self._cluster_cache

        total_nodes = nx_graph.number_of_nodes()
        all_candidates: list[tuple[list[set[str]], str, float]] = []
        levels: list[str | None] = [None, "class", "file"]

        for level in levels:
            if level is None:
                work_graph = nx_graph
            else:
                work_graph = self._cluster_at_level(nx_graph, level)
                if work_graph.number_of_nodes() == 0:
                    continue

            candidates = self._try_all_algorithms(work_graph, min_cluster_size, total_nodes)

            if level is not None:
                candidates = self._map_candidates_to_original(
                    candidates, nx_graph, level, min_cluster_size, total_nodes
                )

            all_candidates.extend(candidates)

            # Check if best coverage at this level is good enough
            if candidates:
                best = max(candidates, key=lambda c: c[2])
                best_coverage = self._coverage(best[0], min_cluster_size, total_nodes)
                logger.info(f"Level {level or 'raw'}: best={best[1]} score={best[2]:.3f} coverage={best_coverage:.3f}")
                if best_coverage >= ClusteringConfig.MIN_COVERAGE_RATIO:
                    break

        # Pick overall best
        if all_candidates:
            best_communities, best_strategy, best_score = max(all_candidates, key=lambda c: c[2])
            if best_score > 0.0:
                self._cluster_cache = self._build_result(best_communities, best_strategy, min_cluster_size, nx_graph)
                return self._cluster_cache

        # Absolute fallback: connected components
        logger.warning("All clustering strategies scored 0, falling back to connected components")
        components = list(nx.connected_components(nx_graph.to_undirected()))
        self._cluster_cache = self._build_result(
            [set(c) for c in components[:target_clusters]], "connected_components", min_cluster_size, nx_graph
        )
        return self._cluster_cache

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
                relevant_edges.append((source_name, target_name))

        filtered_edges = []
        for src, dst in relevant_edges:
            filtered_edges.append(Edge(self.nodes[src], self.nodes[dst]))

        # Create new graph
        sub_graph = CallGraph()
        sub_graph.nodes = relevant_nodes
        sub_graph.edges = filtered_edges

        return sub_graph

    def to_cluster_string(
        self,
        cluster_ids: set[int] | None = None,
        cluster_result: ClusterResult | None = None,
    ) -> str:
        """
        Generate a human-readable string representation of clusters.

        If cluster_ids is provided, only those clusters are included.
        Uses provided cluster_result or calls cluster() if not provided.

        Args:
            cluster_ids: Optional set of cluster IDs to include. If None, includes all.
            cluster_result: Optional pre-computed ClusterResult. If None, calls cluster().

        Returns:
            Formatted string with cluster definitions and inter-cluster connections
        """
        if cluster_result is None:
            cluster_result = self.cluster()

        if not cluster_result.clusters:
            return cluster_result.strategy if cluster_result.strategy in ("empty", "none") else "No clusters found."

        cfg_graph_x = self.to_networkx()

        # Filter clusters if specific IDs requested
        if cluster_ids:
            communities = [
                cluster_result.clusters[cid] for cid in sorted(cluster_ids) if cid in cluster_result.clusters
            ]
            if not communities:
                return f"No clusters found for IDs: {cluster_ids}"
        else:
            # Use all clusters, sorted by ID for consistent output
            communities = [cluster_result.clusters[cid] for cid in sorted(cluster_result.clusters.keys())]

        top_nodes = set().union(*communities) if communities else set()

        cluster_str = self.__cluster_str(communities, cfg_graph_x)
        non_cluster_str = self.__non_cluster_str(cfg_graph_x, top_nodes)
        return cluster_str + non_cluster_str

    def _get_abstract_node_name(self, node_name: str, level: str) -> str:
        parts = node_name.split(self.delimiter)

        if level == "class" and len(parts) > 1:
            return self.delimiter.join(parts[:-1])
        elif level == "file" and len(parts) > 2:
            return self.delimiter.join(parts[:-2])
        elif level == "package" and len(parts) > 3:
            return parts[0]
        else:
            return node_name

    def _cluster_with_algorithm(self, graph: nx.DiGraph, algorithm: str) -> list[set[str]]:
        # Use class-level seed for reproducibility - Louvain/Leiden are non-deterministic without it
        if algorithm == "louvain":
            return list(nx_comm.louvain_communities(graph, seed=ClusteringConfig.CLUSTERING_SEED))
        elif algorithm == "greedy_modularity":
            return list(nx.community.greedy_modularity_communities(graph))
        elif algorithm == "leiden":
            if hasattr(nx_comm, "leiden_communities"):
                return list(nx_comm.leiden_communities(graph, seed=ClusteringConfig.CLUSTERING_SEED))
            logger.warning(
                "leiden_communities not available in this networkx version, "
                "falling back to asynchronous label propagation"
            )
            return list(nx_comm.asyn_lpa_communities(graph, seed=ClusteringConfig.CLUSTERING_SEED))
        else:
            logger.warning(f"Algorithm {algorithm} not supported, defaulting to greedy_modularity")
            return list(nx.community.greedy_modularity_communities(graph))

    def _score_clustering(
        self,
        communities: list[set[str]],
        min_cluster_size: int,
        total_nodes: int,
    ) -> float:
        """Score clustering from 0.0 to 1.0. Coverage is primary, cluster count is a penalty."""
        if not communities or total_nodes == 0:
            return 0.0

        valid_clusters = [c for c in communities if len(c) >= min_cluster_size]
        if not valid_clusters:
            return 0.0

        # Coverage: fraction of nodes in valid clusters (primary driver)
        covered_nodes = sum(len(c) for c in valid_clusters)
        coverage_score = covered_nodes / total_nodes

        # Cluster count penalty: ideal range [total_nodes/20, total_nodes/5]
        cluster_count = len(valid_clusters)
        ideal_min = max(2, total_nodes // 20)
        ideal_max = max(ideal_min + 1, total_nodes // 5)

        if ideal_min <= cluster_count <= ideal_max:
            cluster_count_penalty = 1.0
        elif cluster_count < ideal_min:
            cluster_count_penalty = cluster_count / ideal_min
        else:
            overshoot = cluster_count - ideal_max
            cluster_count_penalty = max(0.0, 1.0 - overshoot / ideal_max)

        return coverage_score * cluster_count_penalty

    def _cluster_at_level(self, graph: nx.DiGraph, level: str) -> nx.DiGraph:
        """Create abstracted graph by grouping nodes at the given level."""
        abstracted = nx.DiGraph()
        node_map: dict[str, str] = {}

        for node in graph.nodes():
            abstract_name = self._get_abstract_node_name(node, level)
            node_map[node] = abstract_name
            if abstract_name not in abstracted:
                abstracted.add_node(abstract_name)

        edge_weights: dict[tuple[str, str], int] = defaultdict(int)
        for src, dst in graph.edges():
            a_src, a_dst = node_map[src], node_map[dst]
            if a_src != a_dst:
                edge_weights[(a_src, a_dst)] += 1

        for (src, dst), weight in edge_weights.items():
            abstracted.add_edge(src, dst, weight=weight)

        return abstracted

    def _try_all_algorithms(
        self,
        graph: nx.DiGraph,
        min_cluster_size: int,
        total_nodes: int,
    ) -> list[tuple[list[set[str]], str, float]]:
        """Try all clustering algorithms and return scored candidates."""
        algorithms = ["louvain", "leiden", "greedy_modularity"]
        candidates: list[tuple[list[set[str]], str, float]] = []
        for algo in algorithms:
            try:
                communities = self._cluster_with_algorithm(graph, algo)
                score = self._score_clustering(communities, min_cluster_size, total_nodes)
                candidates.append((communities, algo, score))
                logger.debug(f"{algo}: score={score:.3f}, clusters={len(communities)}")
            except Exception as e:
                logger.debug(f"Algorithm {algo} failed: {e}")
        return candidates

    def _map_candidates_to_original(
        self,
        candidates: list[tuple[list[set[str]], str, float]],
        original_graph: nx.DiGraph,
        level: str,
        min_cluster_size: int,
        total_nodes: int,
    ) -> list[tuple[list[set[str]], str, float]]:
        """Map abstract community results back to original node names and re-score."""
        abstract_to_original: dict[str, list[str]] = defaultdict(list)
        for node in original_graph.nodes():
            abstract_to_original[self._get_abstract_node_name(node, level)].append(node)

        mapped: list[tuple[list[set[str]], str, float]] = []
        for communities, algo, _ in candidates:
            original_communities: list[set[str]] = []
            for community in communities:
                orig: set[str] = set()
                for abstract_node in community:
                    orig.update(abstract_to_original[abstract_node])
                if orig:
                    original_communities.append(orig)
            new_score = self._score_clustering(original_communities, min_cluster_size, total_nodes)
            mapped.append((original_communities, f"{algo}_level_{level}", new_score))
        return mapped

    def _coverage(self, communities: list[set[str]], min_cluster_size: int, total_nodes: int) -> float:
        """Calculate coverage: fraction of nodes in valid clusters."""
        if total_nodes == 0:
            return 0.0
        valid = [c for c in communities if len(c) >= min_cluster_size]
        return sum(len(c) for c in valid) / total_nodes

    def _build_result(
        self,
        communities: list[set[str]],
        strategy: str,
        min_cluster_size: int,
        nx_graph: nx.DiGraph,
    ) -> ClusterResult:
        """Build ClusterResult from communities."""
        valid_communities = [c for c in communities if len(c) >= min_cluster_size]
        sorted_communities = sorted(valid_communities, key=len, reverse=True)

        clusters: dict[int, set[str]] = {}
        file_to_clusters: dict[str, set[int]] = defaultdict(set)
        cluster_to_files: dict[int, set[str]] = defaultdict(set)

        for cluster_id, nodes in enumerate(sorted_communities, start=1):
            clusters[cluster_id] = set(nodes)
            for node_name in nodes:
                if node_name in nx_graph.nodes:
                    file_path = nx_graph.nodes[node_name].get("file_path")
                    if file_path:
                        file_to_clusters[file_path].add(cluster_id)
                        cluster_to_files[cluster_id].add(file_path)

        logger.info(f"Clustered {nx_graph.number_of_nodes()} nodes into {len(clusters)} clusters using {strategy}")

        return ClusterResult(
            clusters=clusters,
            file_to_clusters=dict(file_to_clusters),
            cluster_to_files=dict(cluster_to_files),
            strategy=strategy,
        )

    @staticmethod
    def __cluster_str(communities: list[set[str]], cfg_graph_x: nx.DiGraph) -> str:
        valid_communities = [c for c in communities if len(c) >= 2]
        top_communities = sorted(valid_communities, key=len, reverse=True)

        # Limit display to avoid overwhelming output
        display_communities = top_communities[: ClusteringConfig.MAX_DISPLAY_CLUSTERS]

        communities_str = f"Cluster Definitions ({len(display_communities)} clusters shown):\n\n"
        for idx, community in enumerate(display_communities, start=1):
            # Group nodes by file, then by class hierarchy within each file
            file_groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
            standalone_nodes: dict[str, list[str]] = defaultdict(list)
            files_in_cluster: set[str] = set()

            for node_name in sorted(community):
                node_data = cfg_graph_x.nodes.get(node_name, {})
                file_path = node_data.get("file_path", "unknown")
                node_type = node_data.get("type")
                files_in_cluster.add(file_path)

                type_label = NodeType.ENTITY_LABELS.get(node_type, "Function")
                parts = node_name.split(".")

                if node_type == NodeType.CLASS_TYPE:
                    # Class node — register as a group header
                    file_groups[file_path][node_name]  # ensure key exists
                elif node_type == NodeType.METHOD_TYPE and len(parts) > 1:
                    # Method — group under its parent class
                    class_name = ".".join(parts[:-1])
                    method_short = parts[-1]
                    file_groups[file_path][class_name].append(f".{method_short} [{type_label}]")
                else:
                    # Standalone function or unresolvable
                    standalone_nodes[file_path].append(f"{node_name} [{type_label}]")

            communities_str += f"Cluster {idx} ({len(community)} nodes, {len(files_in_cluster)} files):\n"

            for file_path in sorted(files_in_cluster):
                communities_str += f"  {file_path}:\n"
                # Render class groups
                for class_name in sorted(file_groups.get(file_path, {})):
                    methods = file_groups[file_path][class_name]
                    communities_str += f"    {class_name} [Class]\n"
                    for method in sorted(methods):
                        communities_str += f"      {method}\n"
                # Render standalone functions
                for func in sorted(standalone_nodes.get(file_path, [])):
                    communities_str += f"    {func}\n"

            communities_str += "\n"

        # Build summarized inter-cluster connections
        node_to_cluster = {node: idx for idx, community in enumerate(display_communities) for node in community}

        # Aggregate inter-cluster edges: (src_cluster, dst_cluster) -> count + sample edges
        inter_cluster_summary: dict[tuple[int, int], list[str]] = defaultdict(list)
        for src, dst in cfg_graph_x.edges():
            src_cluster = node_to_cluster.get(src)
            dst_cluster = node_to_cluster.get(dst)
            if src_cluster is not None and dst_cluster is not None and src_cluster != dst_cluster:
                inter_cluster_summary[(src_cluster, dst_cluster)].append(f"{src} → {dst}")

        inter_cluster_str = "Inter-Cluster Connections:\n\n"
        if inter_cluster_summary:
            for src_cid, dst_cid in sorted(inter_cluster_summary.keys()):
                calls = inter_cluster_summary[(src_cid, dst_cid)]
                src_display = src_cid + 1
                dst_display = dst_cid + 1
                # Show count and up to 3 representative edges
                max_examples = 3
                inter_cluster_str += f"Cluster {src_display} → Cluster {dst_display} ({len(calls)} calls):\n"
                for call in calls[:max_examples]:
                    inter_cluster_str += f"  - {call}\n"
                if len(calls) > max_examples:
                    inter_cluster_str += f"  - ... and {len(calls) - max_examples} more\n"
                inter_cluster_str += "\n"
        else:
            inter_cluster_str += "No inter-cluster connections detected.\n\n"

        return communities_str + inter_cluster_str

    @staticmethod
    def __non_cluster_str(graph_x: nx.DiGraph, top_nodes: set[str]) -> str:
        # Count unclustered edges rather than listing them all
        non_cluster_edges: list[tuple[str, str]] = []
        for src, dst in graph_x.edges():
            if src not in top_nodes or dst not in top_nodes:
                non_cluster_edges.append((src, dst))

        if not non_cluster_edges:
            return ""

        # Summarize by source node to avoid a wall of edges
        max_unclustered_lines = 20
        other_edges_str = f"Unclustered connections ({len(non_cluster_edges)} edges):\n\n"
        for src, dst in sorted(non_cluster_edges)[:max_unclustered_lines]:
            other_edges_str += f"  - {src} → {dst}\n"
        if len(non_cluster_edges) > max_unclustered_lines:
            other_edges_str += f"  - ... and {len(non_cluster_edges) - max_unclustered_lines} more\n"
        other_edges_str += "\n"
        return other_edges_str

    def __str__(self) -> str:
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges\n"
        for _, node in self.nodes.items():
            if node.methods_called_by_me:
                result += f"Method {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}\n"
        return result

    def llm_str(self, size_limit: int = 2_500_000, skip_nodes: list[Node] | None = None) -> str:
        if skip_nodes is None:
            skip_nodes = []

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
            if node.type == NodeType.METHOD_TYPE and len(parts) > 1:
                class_name = self.delimiter.join(parts[:-1])
                method_short = parts[-1]

                for called_method in node.methods_called_by_me:
                    called_parts = called_method.split(self.delimiter)
                    if len(called_parts) > 1:
                        called_class = self.delimiter.join(called_parts[:-1])
                        called_short = called_parts[-1]
                        class_calls[class_name][called_class].append(f"{method_short}→{called_short}")
                    else:
                        class_calls[class_name][called_method].append(f"{method_short}→{called_method}")
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
            result += f"Class {class_name} → {'; '.join(target_strs)}\n"

        for func_call in function_calls:
            result += func_call + "\n"

        logger.info(f"[CallGraph] Class-level summary: {len(result)} characters")
        return result
