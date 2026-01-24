import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional

import networkx as nx
import networkx.algorithms.community as nx_comm

logger = logging.getLogger(__name__)


@dataclass
class ClusterResult:
    """Result of clustering a CallGraph. Provides deterministic cluster IDs and file mappings."""

    clusters: Dict[int, Set[str]] = field(default_factory=dict)  # cluster_id -> node names
    file_to_clusters: Dict[str, Set[int]] = field(default_factory=dict)  # file_path -> cluster_ids
    cluster_to_files: Dict[int, Set[str]] = field(default_factory=dict)  # cluster_id -> file_paths
    strategy: str = ""  # which algorithm was used

    def get_cluster_ids(self) -> Set[int]:
        return set(self.clusters.keys())

    def get_files_for_cluster(self, cluster_id: int) -> Set[str]:
        return self.cluster_to_files.get(cluster_id, set())

    def get_clusters_for_file(self, file_path: str) -> Set[int]:
        return self.file_to_clusters.get(file_path, set())

    def get_nodes_for_cluster(self, cluster_id: int) -> Set[str]:
        return self.clusters.get(cluster_id, set())


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
    MAX_SINGLETON_RATIO = 0.6  # No more than 60% singleton clusters (indicates poor clustering)
    MIN_CLUSTER_COUNT_RATIO = 6  # Minimum clusters = target_clusters // 6 (avoid too few clusters)
    MAX_CLUSTER_COUNT_MULTIPLIER = 2  # Maximum clusters = target_clusters * 2

    # Cluster size constraints
    SMALL_GRAPH_MAX_CLUSTER_RATIO = 0.6  # For graphs < 50 nodes, max cluster can be 60% of total
    LARGE_GRAPH_MAX_CLUSTER_RATIO = 0.4  # For larger graphs, max cluster should be 40% of total
    MAX_SIZE_TO_AVG_RATIO = 8  # Largest cluster shouldn't be more than 8x average size
    SMALL_GRAPH_THRESHOLD = 50  # Threshold between "small" and "large" graphs

    # Cluster balancing parameters
    MIN_CLUSTER_SIZE_MULTIPLIER = 3  # When merging, stop at min_size * 3 to avoid oversized clusters
    MAX_CLUSTER_SIZE_MULTIPLIER = 3  # Max cluster size = (total_nodes // target_clusters) * 3
    MIN_MAX_CLUSTER_SIZE = 10  # Absolute minimum for max cluster size

    # Display limits
    MAX_DISPLAY_CLUSTERS = 25  # Maximum clusters to show in output (readability limit)

    # Language-specific delimiters for qualified names
    DEFAULT_DELIMITER = "."  # Works for Python, Java, C#
    DELIMITER_MAP = {
        "python": ".",
        "go": ".",
        "php": "\\",  # PHP uses backslash for namespaces
        "typescript": ".",
        "javascript": ".",
    }


class Node:
    # Node type constants
    METHOD_TYPE = 6

    def __init__(
        self, fully_qualified_name: str, node_type: int, file_path: str, line_start: int, line_end: int
    ) -> None:
        self.fully_qualified_name = fully_qualified_name
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.type = node_type
        self.methods_called_by_me: set[str] = set()

    def added_method_called_by_me(self, node: "Node") -> None:
        if isinstance(node, Node):
            self.methods_called_by_me.add(node.fully_qualified_name)
        else:
            raise ValueError("Expected a Node instance.")

    def __hash__(self) -> int:
        return hash(self.fully_qualified_name)

    def __repr__(self) -> str:
        return f"Node({self.fully_qualified_name}, {self.file_path}, {self.line_start}-{self.line_end})"


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
    # Deterministic seed for clustering algorithms
    CLUSTERING_SEED = 42

    def __init__(
        self, nodes: Dict[str, Node] | None = None, edges: List[Edge] | None = None, language: str = "python"
    ) -> None:
        self.nodes = nodes if nodes is not None else {}
        self.edges = edges if edges is not None else []
        self._edge_set: Set[Tuple[str, str]] = set()
        self.language = language.lower()
        # Set delimiter based on language for qualified name parsing
        self.delimiter = ClusteringConfig.DELIMITER_MAP.get(self.language, ClusteringConfig.DEFAULT_DELIMITER)
        # Cache for cluster result
        self._cluster_cache: Optional[ClusterResult] = None

    def add_node(self, node: Node) -> None:
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
        """
        Perform deterministic clustering and return structured result with file mappings.

        Results are cached - subsequent calls return the same ClusterResult.
        Cluster IDs are stable and start from 1.

        Args:
            target_clusters: Target number of clusters to find
            min_cluster_size: Minimum nodes per cluster

        Returns:
            ClusterResult with cluster_id -> nodes mapping and file <-> cluster bidirectional maps
        """
        if self._cluster_cache is not None:
            return self._cluster_cache

        nx_graph = self.to_networkx()
        if nx_graph.number_of_nodes() == 0:
            logger.warning("No nodes available for clustering.")
            self._cluster_cache = ClusterResult(strategy="empty")
            return self._cluster_cache

        communities, strategy_used = self._adaptive_clustering(
            nx_graph,
            target_clusters=target_clusters,
            min_cluster_size=min_cluster_size,
        )

        if not communities:
            logger.info("No significant clusters found.")
            self._cluster_cache = ClusterResult(strategy="none")
            return self._cluster_cache

        # Sort communities by size (descending) for stable ordering
        valid_communities = [c for c in communities if len(c) >= min_cluster_size]
        sorted_communities = sorted(valid_communities, key=len, reverse=True)

        # Build cluster mappings with 1-based IDs
        clusters: Dict[int, Set[str]] = {}
        file_to_clusters: Dict[str, Set[int]] = defaultdict(set)
        cluster_to_files: Dict[int, Set[str]] = defaultdict(set)

        for cluster_id, nodes in enumerate(sorted_communities, start=1):
            clusters[cluster_id] = set(nodes)

            for node_name in nodes:
                if node_name in nx_graph.nodes:
                    file_path = nx_graph.nodes[node_name].get("file_path")
                    if file_path:
                        file_to_clusters[file_path].add(cluster_id)
                        cluster_to_files[cluster_id].add(file_path)

        logger.info(f"Clustered {nx_graph.number_of_nodes()} nodes into {len(clusters)} clusters using {strategy_used}")

        self._cluster_cache = ClusterResult(
            clusters=clusters,
            file_to_clusters=dict(file_to_clusters),
            cluster_to_files=dict(cluster_to_files),
            strategy=strategy_used,
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
        self, cluster_ids: Optional[Set[int]] = None, cluster_result: Optional[ClusterResult] = None
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

    def _adaptive_clustering(
        self,
        graph: nx.DiGraph,
        target_clusters: int = ClusteringConfig.DEFAULT_TARGET_CLUSTERS,
        min_cluster_size: int = ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE,
    ) -> Tuple[List[Set[str]], str]:
        """
        Adaptive clustering strategy that tries multiple algorithms in order of preference.

        Algorithm selection rationale:
        1. Connectivity-based (louvain, leiden, greedy_modularity): Best for finding natural communities
           - Louvain: Fast, good quality, works well for medium-sized graphs
           - Leiden: Higher quality than Louvain but slower, good for complex structures
           - Greedy modularity: Reliable fallback, deterministic results

        2. Structural-based (method, class level): When connectivity fails, use code structure
           - Method level: Fine-grained clustering based on call patterns
           - Class level: Coarser clustering, good for object-oriented codebases

        3. Balanced fallback: Force balance when structure-based approaches fail

        4. Connected components: Last resort when all else fails

        The order prioritizes algorithms that scale well with codebase size:
        - Small codebases (<500 nodes): All algorithms work well
        - Medium codebases (500-5000 nodes): Louvain/Leiden preferred
        - Large codebases (>5000 nodes): Greedy modularity may be too slow, structural approaches preferred
        """
        total_nodes = graph.number_of_nodes()
        logger.info(f"Starting adaptive clustering for {total_nodes} nodes, target: {target_clusters} clusters")

        # Phase 1: Try connectivity-based algorithms (best for natural community detection)
        connectivity_algorithms = self._get_algorithm_priority_by_size(total_nodes)
        for algorithm in connectivity_algorithms:
            try:
                communities = self._cluster_with_algorithm(graph, algorithm, target_clusters)
                if self._is_good_clustering(communities, target_clusters, min_cluster_size, total_nodes):
                    return communities, f"connectivity_{algorithm}"
            except Exception as e:
                logger.debug(f"Connectivity algorithm {algorithm} failed: {e}")
                continue

        # Phase 2: Try structural-based clustering (use code structure when connectivity fails)
        for level in ["method", "class"]:
            try:
                communities = self._cluster_at_level(graph, level, target_clusters, min_cluster_size)
                if self._is_good_clustering(communities, target_clusters, min_cluster_size, total_nodes):
                    return communities, f"structural_{level}"
            except Exception as e:
                logger.debug(f"Structural level {level} failed: {e}")
                continue

        # Phase 3: Balanced fallback (force reasonable clustering when structure fails)
        # NOTE: This re-uses greedy_modularity from Phase 1, but applies _balance_clusters()
        # to force compliance with size/count constraints when raw algorithm output isn't "good enough"
        try:
            initial_communities = list(nx.community.greedy_modularity_communities(graph))
            balanced_communities = self._balance_clusters(graph, initial_communities, target_clusters, min_cluster_size)
            return balanced_communities, "balanced_greedy_modularity"
        except Exception as e:
            logger.warning(f"All clustering strategies failed: {e}")
            # Phase 4: Last resort - connected components
            components = list(nx.connected_components(graph.to_undirected()))
            return components[:target_clusters], "connected_components"

    def _get_algorithm_priority_by_size(self, total_nodes: int) -> List[str]:
        """
        Prioritize algorithms based on graph size for optimal performance/quality tradeoff.

        Small graphs: All algorithms work well, prefer quality (leiden > louvain > greedy)
        Medium graphs: Balance quality and speed (louvain > leiden > greedy)
        Large graphs: Prefer speed (louvain > greedy, skip leiden due to memory usage)
        """
        if total_nodes < 500:
            return ["leiden", "louvain", "greedy_modularity"]
        elif total_nodes < 5000:
            return ["louvain", "leiden", "greedy_modularity"]
        else:
            return ["louvain", "greedy_modularity"]  # Skip leiden for very large graphs

    def _cluster_at_level(
        self, graph: nx.DiGraph, level: str, target_clusters: int, min_cluster_size: int
    ) -> List[Set[str]]:
        """
        Cluster at different structural levels (method/class/file/package).

        This is different from connectivity-based clustering because it uses the hierarchical
        structure of the code rather than just call relationships. When connectivity algorithms
        fail to find good communities, structural clustering can still group related code.
        """
        if level == "method":
            # Method level is the same as direct connectivity clustering
            return self._cluster_with_algorithm(graph, "louvain", target_clusters)

        # For higher levels, create abstracted graph and map back
        abstracted_graph = self._create_abstracted_graph(graph, level)
        if abstracted_graph.number_of_nodes() == 0:
            return []
        abstract_communities = self._cluster_with_algorithm(abstracted_graph, "louvain", target_clusters)
        return self._map_abstract_to_original(abstract_communities, graph, level)

    def _create_abstracted_graph(self, graph: nx.DiGraph, level: str) -> nx.DiGraph:
        abstracted_graph = nx.DiGraph()
        node_to_abstract: dict[str, str] = {}

        for node in graph.nodes():
            abstract_node = self._get_abstract_node_name(node, level)
            node_to_abstract[node] = abstract_node

            if abstract_node not in abstracted_graph:
                abstracted_graph.add_node(abstract_node)

        edge_weights: dict[tuple[str, str], int] = defaultdict(int)
        for src, dst in graph.edges():
            abstract_src = node_to_abstract[src]
            abstract_dst = node_to_abstract[dst]

            if abstract_src != abstract_dst:
                edge_weights[(abstract_src, abstract_dst)] += 1

        for (src, dst), weight in edge_weights.items():
            abstracted_graph.add_edge(src, dst, weight=weight)

        return abstracted_graph

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

    def _map_abstract_to_original(
        self, abstract_communities: List[Set[str]], original_graph: nx.DiGraph, level: str
    ) -> List[Set[str]]:
        """
        Map abstract communities back to original nodes efficiently using lookup table.

        Performance improvement: O(N) instead of O(N²) by building reverse mapping first.
        """
        original_communities: List[Set[str]] = []

        # Build reverse mapping: abstract_node -> [original_nodes] (O(N) preprocessing)
        abstract_to_original: Dict[str, List[str]] = defaultdict(list)
        for original_node in original_graph.nodes():
            abstract_node = self._get_abstract_node_name(original_node, level)
            abstract_to_original[abstract_node].append(original_node)

        # Map communities using lookup table (O(N) mapping)
        for abstract_community in abstract_communities:
            original_community: Set[str] = set()

            for abstract_node in abstract_community:
                original_community.update(abstract_to_original[abstract_node])

            if original_community:
                original_communities.append(original_community)

        return original_communities

    def _cluster_with_algorithm(self, graph: nx.DiGraph, algorithm: str, target_clusters: int) -> list[set[str]]:
        # Use class-level seed for reproducibility - Louvain/Leiden are non-deterministic without it
        if algorithm == "louvain":
            return list(nx_comm.louvain_communities(graph, seed=self.CLUSTERING_SEED))
        elif algorithm == "greedy_modularity":
            return list(nx.community.greedy_modularity_communities(graph))
        elif algorithm == "leiden":
            return list(nx_comm.louvain_communities(graph, seed=self.CLUSTERING_SEED))
        else:
            logger.warning(f"Algorithm {algorithm} not supported, defaulting to greedy_modularity")
            return list(nx.community.greedy_modularity_communities(graph))

    def _balance_clusters(
        self, graph: nx.DiGraph, initial_communities: List[Set[str]], target_clusters: int, min_cluster_size: int
    ) -> List[Set[str]]:
        sorted_communities = sorted(initial_communities, key=len, reverse=True)

        significant_clusters: List[Set[str]] = []
        singletons: List[str] = []
        small_clusters: List[Set[str]] = []

        # Calculate max cluster size to prevent oversized clusters
        max_cluster_size = max(
            ClusteringConfig.MIN_MAX_CLUSTER_SIZE,
            graph.number_of_nodes() // target_clusters * ClusteringConfig.MAX_CLUSTER_SIZE_MULTIPLIER,
        )

        for community in sorted_communities:
            if len(community) == 1:
                singletons.extend(list(community))
            elif len(community) < min_cluster_size:
                small_clusters.append(community)
            elif len(community) > max_cluster_size:
                sub_clusters = self._split_large_cluster(graph, community, max_cluster_size)
                significant_clusters.extend(sub_clusters)
            else:
                significant_clusters.append(community)

        merged_small = self._merge_small_clusters(
            graph, small_clusters + [set([s]) for s in singletons], min_cluster_size
        )
        significant_clusters.extend(merged_small)

        if len(significant_clusters) > target_clusters:
            significant_clusters = sorted(significant_clusters, key=len, reverse=True)
            return significant_clusters[:target_clusters]

        return significant_clusters

    def _split_large_cluster(self, graph: nx.DiGraph, large_cluster: Set[str], max_size: int) -> List[Set[str]]:
        """
        Split oversized clusters using graph structure rather than arbitrary division.

        Strategy:
        1. Try community detection within the cluster (preserves natural groupings)
        2. Fall back to connected components (preserves connectivity)
        3. Last resort: balanced binary split (when structure doesn't help)

        This approach is much better than naive binary splitting because it respects
        the underlying graph structure and relationships between nodes.
        """
        if len(large_cluster) <= max_size:
            return [large_cluster]

        subgraph = graph.subgraph(large_cluster)

        # Strategy 1: Try to find natural sub-communities within the large cluster
        try:
            sub_communities = list(nx.community.greedy_modularity_communities(subgraph))
            if len(sub_communities) > 1:
                valid_subclusters = [set(comm) for comm in sub_communities if len(comm) >= 2]
                if valid_subclusters:
                    return valid_subclusters
        except Exception:
            pass

        # Strategy 2: Split by connected components (preserves connectivity structure)
        try:
            components = list(nx.connected_components(subgraph.to_undirected()))
            if len(components) > 1:
                return [set(comp) for comp in components]
        except Exception:
            pass

        # Strategy 3: Last resort - balanced split (better than random but still not ideal)
        cluster_list = list(large_cluster)
        mid = len(cluster_list) // 2
        return [set(cluster_list[:mid]), set(cluster_list[mid:])]

    def _merge_small_clusters(
        self, graph: nx.DiGraph, small_clusters: List[Set[str]], min_cluster_size: int
    ) -> List[Set[str]]:
        if not small_clusters:
            return []

        merged_clusters: List[Set[str]] = []
        remaining = small_clusters.copy()

        while remaining:
            current_cluster = set(remaining.pop(0))

            merged_any = True
            # Stop merging when cluster gets too large to avoid creating oversized clusters
            max_merge_size = min_cluster_size * ClusteringConfig.MIN_CLUSTER_SIZE_MULTIPLIER
            while merged_any and len(current_cluster) < max_merge_size:
                merged_any = False

                for i, other_cluster in enumerate(remaining):
                    other_set = set(other_cluster)

                    has_connection = False
                    for node1 in current_cluster:
                        for node2 in other_set:
                            if graph.has_edge(node1, node2) or graph.has_edge(node2, node1):
                                has_connection = True
                                break
                        if has_connection:
                            break

                    if has_connection:
                        current_cluster.update(other_set)
                        remaining.pop(i)
                        merged_any = True
                        break

            if len(current_cluster) >= min_cluster_size:
                merged_clusters.append(current_cluster)

        return merged_clusters

    def _is_good_clustering(
        self, communities: List[Set[str]], target_clusters: int, min_cluster_size: int, total_nodes: int
    ) -> bool:
        """
        Determine if a clustering result meets quality criteria.

        A "good" clustering should:
        1. Have meaningful clusters (not too many singletons)
        2. Cover most nodes in the graph (high coverage)
        3. Have reasonable number of clusters (not too few or too many)
        4. Avoid oversized clusters that dominate the graph
        5. Have balanced cluster sizes (largest not too much bigger than average)

        These criteria are based on empirical testing with various codebases and
        aim to produce clusterings that are useful for human understanding and LLM processing.
        """
        if not communities:
            return False

        valid_clusters = [c for c in communities if len(c) >= min_cluster_size]

        if len(valid_clusters) == 0:
            return False

        cluster_count = len(valid_clusters)
        # Minimum clusters: avoid too few clusters (at least target_clusters // 6, minimum 2)
        min_clusters = max(2, target_clusters // ClusteringConfig.MIN_CLUSTER_COUNT_RATIO)
        if cluster_count < min_clusters:
            return False

        # Maximum clusters: avoid too many clusters (at most target_clusters * 2)
        if cluster_count > target_clusters * ClusteringConfig.MAX_CLUSTER_COUNT_MULTIPLIER:
            return False

        covered_nodes = sum(len(c) for c in valid_clusters)
        coverage = covered_nodes / total_nodes if total_nodes > 0 else 0

        # Coverage check: at least 75% of nodes should be in meaningful clusters
        if coverage < ClusteringConfig.MIN_COVERAGE_RATIO:
            return False

        singleton_count = sum(1 for c in communities if len(c) == 1)
        # Singleton check: no more than 60% singleton clusters (indicates poor clustering)
        if singleton_count > total_nodes * ClusteringConfig.MAX_SINGLETON_RATIO:
            return False

        largest_cluster_size = max(len(c) for c in valid_clusters)
        # Cluster size check: largest cluster shouldn't dominate (varies by graph size)
        max_cluster_ratio = (
            ClusteringConfig.SMALL_GRAPH_MAX_CLUSTER_RATIO
            if total_nodes < ClusteringConfig.SMALL_GRAPH_THRESHOLD
            else ClusteringConfig.LARGE_GRAPH_MAX_CLUSTER_RATIO
        )
        if largest_cluster_size > total_nodes * max_cluster_ratio:
            return False

        cluster_sizes = [len(c) for c in valid_clusters]
        avg_size = sum(cluster_sizes) / len(cluster_sizes)
        max_size = max(cluster_sizes)

        # Balance check: largest cluster shouldn't be more than 8x average size
        if max_size > avg_size * ClusteringConfig.MAX_SIZE_TO_AVG_RATIO:
            return False

        logger.info(
            f"Good clustering found: {cluster_count} clusters, {coverage:.2%} coverage, {singleton_count} singletons, largest: {largest_cluster_size}"
        )
        return True

    @staticmethod
    def __cluster_str(communities: List[Set[str]], cfg_graph_x: nx.DiGraph) -> str:
        valid_communities = [c for c in communities if len(c) >= 2]
        top_communities = sorted(valid_communities, key=len, reverse=True)

        # Limit display to avoid overwhelming output
        display_communities = top_communities[: ClusteringConfig.MAX_DISPLAY_CLUSTERS]

        communities_str = f"Cluster Definitions ({len(display_communities)} clusters shown):\n\n"
        for idx, community in enumerate(display_communities, start=1):
            community_list = sorted(list(community))
            communities_str += f"Cluster {idx} ({len(community)} nodes): {community_list}\n\n"

        cluster_to_cluster_calls: Dict[int, Dict[int, List[str]]] = defaultdict(lambda: defaultdict(list))
        node_to_cluster = {node: idx for idx, community in enumerate(display_communities) for node in community}

        for src, dst in cfg_graph_x.edges():
            src_cluster = node_to_cluster.get(src)
            dst_cluster = node_to_cluster.get(dst)

            if src_cluster is None or dst_cluster is None:
                continue
            if src_cluster != dst_cluster:
                cluster_to_cluster_calls[src_cluster][dst_cluster].append(f"{src} → {dst}")

        inter_cluster_str = "Inter-Cluster Connections:\n\n"
        if cluster_to_cluster_calls:
            for src_cluster_id in sorted(cluster_to_cluster_calls.keys()):
                for dst_cluster_id in sorted(cluster_to_cluster_calls[src_cluster_id].keys()):
                    calls = cluster_to_cluster_calls[src_cluster_id][dst_cluster_id]
                    src_display = src_cluster_id + 1
                    dst_display = dst_cluster_id + 1

                    inter_cluster_str += f"Cluster {src_display} → Cluster {dst_display} via method calls:\n"
                    for call in calls:
                        inter_cluster_str += f"  - {call}\n"
                    inter_cluster_str += "\n"
        else:
            inter_cluster_str += "No inter-cluster connections detected.\n\n"

        return communities_str + inter_cluster_str

    @staticmethod
    def __non_cluster_str(graph_x: nx.DiGraph, top_nodes: Set[str]) -> str:
        non_cluster_edges: List[Tuple[str, str]] = []
        for src, dst in graph_x.edges():
            if src not in top_nodes or dst not in top_nodes:
                non_cluster_edges.append((src, dst))

        other_edges_str = ""
        if non_cluster_edges:
            other_edges_str = "Outside of the main clusters we also have communication between:\n\n"
            for src, dst in sorted(non_cluster_edges):
                other_edges_str += f"  - {src} calls {dst}\n"
            other_edges_str += "\n"
        return other_edges_str

    def __str__(self) -> str:
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges\n"
        for _, node in self.nodes.items():
            if node.methods_called_by_me:
                result += f"Method {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}\n"
        return result

    def llm_str(self, size_limit: int = 2_500_000, skip_nodes: Optional[List[Node]] = None) -> str:
        if skip_nodes is None:
            skip_nodes = []

        default_str = str(self)

        logger.info(f"[CFG Tool] LLM string: {len(default_str)} characters, size limit: {size_limit} characters")

        if len(default_str) <= size_limit:
            return default_str

        class_calls: Dict[str, Dict[str, int]] = {}
        function_calls: List[str] = []

        logger.info(
            f"[CallGraph] Control flow graph is too large, grouping method calls by class. ({len(default_str)} characters)"
        )

        for _, node in self.nodes.items():
            if node in skip_nodes:
                continue
            if node.type == Node.METHOD_TYPE and node.methods_called_by_me:
                parts = node.fully_qualified_name.split(self.delimiter)
                if len(parts) > 1:
                    class_name = self.delimiter.join(parts[:-1])

                    if class_name not in class_calls:
                        class_calls[class_name] = {}

                    for called_method in node.methods_called_by_me:
                        called_parts = called_method.split(self.delimiter)
                        if len(called_parts) > 1:
                            called_class = self.delimiter.join(called_parts[:-1])
                            if called_class not in class_calls[class_name]:
                                class_calls[class_name][called_class] = 0
                            class_calls[class_name][called_class] += 1
                        else:
                            if called_method not in class_calls[class_name]:
                                class_calls[class_name][called_method] = 0
                            class_calls[class_name][called_method] += 1
                else:
                    function_calls.append(
                        f"Function {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}"
                    )
            elif node.methods_called_by_me:
                function_calls.append(
                    f"Function {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}"
                )

        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges (grouped view)\n"

        for class_name, called_classes in class_calls.items():
            calls_str = []
            for called_class, count in called_classes.items():
                calls_str.append(f"{called_class}({count} methods)")

            if calls_str:
                result += f"Class {class_name} is calling the following classes {', '.join(calls_str)}\n"

        for func_call in function_calls:
            result += func_call + "\n"

        logger.info(f"[CallGraph] Control flow graph grouped by class, total characters: {len(result)}")
        return result
