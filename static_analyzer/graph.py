import logging
from collections import defaultdict

import networkx as nx

logger = logging.getLogger(__name__)


class Node:
    def __init__(self, fully_qualified_name, node_type, file_path, line_start, line_end):
        self.fully_qualified_name = fully_qualified_name
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.type = node_type
        self.methods_called_by_me = set()

    def added_method_called_by_me(self, node):
        """Add a calling method to this node."""
        if isinstance(node, Node):
            self.methods_called_by_me.add(node.fully_qualified_name)
        else:
            raise ValueError("Expected a Node instance.")

    def __hash__(self):
        return hash(self.fully_qualified_name)

    def __repr__(self):
        return f"Node({self.fully_qualified_name}, {self.file_path}, {self.line_start}-{self.line_end})"


class Edge:
    def __init__(self, src_node: Node, dst_node: Node):
        self.src_node = src_node
        self.dst_node = dst_node

    def get_source(self):
        return self.src_node.fully_qualified_name

    def get_destination(self):
        return self.dst_node.fully_qualified_name

    def __repr__(self):
        return f"Edge({self.src_node.fully_qualified_name} -> {self.dst_node.fully_qualified_name})"


class CallGraph:
    def __init__(self, nodes=None, edges=None):
        self.nodes = nodes if nodes is not None else {}
        self.edges = edges if edges is not None else []
        self._edge_set = set()  # Track existing edges to avoid duplicates

    def add_node(self, node):
        if node.fully_qualified_name not in self.nodes:
            self.nodes[node.fully_qualified_name] = node

    def add_edge(self, src_name, dst_name):
        if src_name not in self.nodes or dst_name not in self.nodes:
            raise ValueError("Both source and destination nodes must exist in the graph.")

        # Check for duplicate edges
        edge_key = (src_name, dst_name)
        if edge_key in self._edge_set:
            return  # Edge already exists

        edge = Edge(self.nodes[src_name], self.nodes[dst_name])
        self.edges.append(edge)
        self._edge_set.add(edge_key)

        # Update the destination node's calling methods
        self.nodes[src_name].added_method_called_by_me(self.nodes[dst_name])

    def to_networkx(self):
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

    def to_cluster_string(self) -> str:
        cfg_graph_x = self.to_networkx()
        if cfg_graph_x.number_of_nodes() == 0:
            summary = "No nodes available for clustering."
            logger.warning(summary)
            return summary

        # Use adaptive clustering to get better balanced clusters
        communities, strategy_used = self._adaptive_clustering(cfg_graph_x, target_clusters=20, min_cluster_size=2)

        if not communities:
            summary = "No significant clusters found."
            logger.info(summary)
            return summary

        logger.info(f"Used clustering strategy: {strategy_used}, found {len(communities)} clusters")
        # Get all nodes that are in clusters
        top_nodes = set().union(*communities) if communities else set()

        cluster_str = self.__cluster_str(communities, cfg_graph_x)
        non_cluster_str = self.__non_cluster_str(cfg_graph_x, top_nodes)
        return cluster_str + non_cluster_str

    def _adaptive_clustering(self, graph, target_clusters=20, min_cluster_size=2):
        total_nodes = graph.number_of_nodes()
        logger.info(f"Starting adaptive clustering for {total_nodes} nodes, target: {target_clusters} clusters")

        # Strategy 1: Try connectivity-based algorithms first (preserve actual call relationships)
        for algorithm in ['louvain', 'leiden', 'greedy_modularity']:
            try:
                communities = self._cluster_with_algorithm(graph, algorithm, target_clusters)
                if self._is_good_clustering(communities, target_clusters, min_cluster_size, total_nodes):
                    return communities, f"connectivity_{algorithm}"
            except Exception as e:
                logger.debug(f"Connectivity algorithm {algorithm} failed: {e}")
                continue

        # Strategy 2: Try structural clustering only if connectivity fails
        for level in ['method', 'class']:  # Skip file/package levels as they create too large clusters
            try:
                communities = self._cluster_at_level(graph, level, target_clusters, min_cluster_size)
                if self._is_good_clustering(communities, target_clusters, min_cluster_size, total_nodes):
                    return communities, f"structural_{level}"
            except Exception as e:
                logger.debug(f"Structural level {level} failed: {e}")
                continue

        # Strategy 3: Hybrid approach - balance existing clusters
        try:
            # Fall back to greedy modularity and then balance
            initial_communities = list(nx.community.greedy_modularity_communities(graph))
            balanced_communities = self._balance_clusters(graph, initial_communities, target_clusters, min_cluster_size)
            return balanced_communities, "balanced_greedy_modularity"
        except Exception as e:
            logger.warning(f"All clustering strategies failed: {e}")
            # Last resort: create clusters by connected components
            components = list(nx.connected_components(graph.to_undirected()))
            return components[:target_clusters], "connected_components"

    def _cluster_at_level(self, graph, level, target_clusters, min_cluster_size):
        """
        Cluster by grouping nodes at different abstraction levels.
        """
        if level == 'method':
            # Use original graph (method level)
            return self._cluster_with_algorithm(graph, 'louvain', target_clusters)

        # Create abstracted graph
        abstracted_graph = self._create_abstracted_graph(graph, level)
        if abstracted_graph.number_of_nodes() == 0:
            return []

        # Cluster the abstracted graph
        abstract_communities = self._cluster_with_algorithm(abstracted_graph, 'louvain', target_clusters)

        # Map back to original nodes
        return self._map_abstract_to_original(abstract_communities, graph, level)

    def _create_abstracted_graph(self, graph, level):
        """
        Create an abstracted version of the graph by grouping nodes.
        """
        abstracted_graph = nx.DiGraph()
        node_to_abstract = {}

        # Map each node to its abstract representation
        for node in graph.nodes():
            abstract_node = self._get_abstract_node_name(node, level)
            node_to_abstract[node] = abstract_node

            # Add node attributes from original graph
            if abstract_node not in abstracted_graph:
                abstracted_graph.add_node(abstract_node)

        # Add edges between abstract nodes
        edge_weights = defaultdict(int)
        for src, dst in graph.edges():
            abstract_src = node_to_abstract[src]
            abstract_dst = node_to_abstract[dst]

            if abstract_src != abstract_dst:  # Don't add self-loops
                edge_weights[(abstract_src, abstract_dst)] += 1

        # Add weighted edges
        for (src, dst), weight in edge_weights.items():
            abstracted_graph.add_edge(src, dst, weight=weight)

        return abstracted_graph

    def _get_abstract_node_name(self, node_name, level):
        """
        Get the abstract node name based on the abstraction level.
        """
        parts = node_name.split('.')

        if level == 'class' and len(parts) > 1:
            # Remove method name, keep class: package.file.class
            return '.'.join(parts[:-1])
        elif level == 'file' and len(parts) > 2:
            # Remove class and method, keep file: package.file
            return '.'.join(parts[:-2])
        elif level == 'package' and len(parts) > 3:
            # Keep only package: package
            return parts[0]
        else:
            # If we can't abstract further, return original
            return node_name

    def _map_abstract_to_original(self, abstract_communities, original_graph, level):
        """
        Map abstract communities back to original node communities.
        """
        original_communities = []

        for abstract_community in abstract_communities:
            original_community = set()

            for abstract_node in abstract_community:
                # Find all original nodes that map to this abstract node
                for original_node in original_graph.nodes():
                    if self._get_abstract_node_name(original_node, level) == abstract_node:
                        original_community.add(original_node)

            if original_community:  # Only add non-empty communities
                original_communities.append(original_community)

        return original_communities

    def _cluster_with_algorithm(self, graph, algorithm, target_clusters):
        """
        Apply specific clustering algorithm.
        """
        if algorithm == 'louvain':
            try:
                import networkx.algorithms.community as nx_comm
                return list(nx_comm.louvain_communities(graph))
            except ImportError:
                # Fallback to greedy modularity
                return list(nx.community.greedy_modularity_communities(graph))

        elif algorithm == 'greedy_modularity':
            return list(nx.community.greedy_modularity_communities(graph))

        elif algorithm == 'leiden':
            try:
                # Leiden is not in standard networkx, fallback to louvain
                import networkx.algorithms.community as nx_comm
                return list(nx_comm.louvain_communities(graph))
            except ImportError:
                return list(nx.community.greedy_modularity_communities(graph))

        elif algorithm == 'spectral':
            try:
                from sklearn.cluster import SpectralClustering
                import numpy as np

                # Convert to adjacency matrix
                adj_matrix = nx.adjacency_matrix(graph).todense()
                if adj_matrix.shape[0] < target_clusters:
                    target_clusters = max(2, adj_matrix.shape[0] // 2)

                clustering = SpectralClustering(n_clusters=target_clusters, random_state=42)
                labels = clustering.fit_predict(adj_matrix)

                # Group nodes by cluster labels
                communities = defaultdict(set)
                for i, node in enumerate(graph.nodes()):
                    communities[labels[i]].add(node)

                return list(communities.values())
            except ImportError:
                return list(nx.community.greedy_modularity_communities(graph))

        elif algorithm == 'hierarchical':
            try:
                from sklearn.cluster import AgglomerativeClustering
                import numpy as np

                # Convert to adjacency matrix
                adj_matrix = nx.adjacency_matrix(graph).todense()
                if adj_matrix.shape[0] < target_clusters:
                    target_clusters = max(2, adj_matrix.shape[0] // 2)

                clustering = AgglomerativeClustering(n_clusters=target_clusters)
                labels = clustering.fit_predict(adj_matrix)

                # Group nodes by cluster labels
                communities = defaultdict(set)
                for i, node in enumerate(graph.nodes()):
                    communities[labels[i]].add(node)

                return list(communities.values())
            except ImportError:
                return list(nx.community.greedy_modularity_communities(graph))

        else:
            # Default to greedy modularity
            return list(nx.community.greedy_modularity_communities(graph))

    def _balance_clusters(self, graph, initial_communities, target_clusters, min_cluster_size):
        """
        Post-process clusters to achieve better balance.
        """
        # Sort communities by size (largest first)
        sorted_communities = sorted(initial_communities, key=len, reverse=True)

        # Separate significant clusters from singletons
        significant_clusters = []
        singletons = []
        small_clusters = []

        max_cluster_size = max(10,
                               graph.number_of_nodes() // target_clusters * 3)  # Allow clusters up to 3x average size

        for community in sorted_communities:
            if len(community) == 1:
                singletons.extend(list(community))
            elif len(community) < min_cluster_size:
                small_clusters.append(community)
            elif len(community) > max_cluster_size:
                # Split large clusters
                sub_clusters = self._split_large_cluster(graph, community, max_cluster_size)
                significant_clusters.extend(sub_clusters)
            else:
                significant_clusters.append(community)

        # Merge small clusters and singletons
        merged_small = self._merge_small_clusters(graph, small_clusters + [set([s]) for s in singletons],
                                                  min_cluster_size)
        significant_clusters.extend(merged_small)

        # If we have too many clusters, merge the smallest ones
        if len(significant_clusters) > target_clusters:
            significant_clusters = sorted(significant_clusters, key=len, reverse=True)
            return significant_clusters[:target_clusters]

        return significant_clusters

    def _split_large_cluster(self, graph, large_cluster, max_size):
        """
        Split a large cluster into smaller sub-clusters.
        """
        if len(large_cluster) <= max_size:
            return [large_cluster]

        # Create subgraph for this cluster
        subgraph = graph.subgraph(large_cluster)

        # Try to split using community detection on the subgraph
        try:
            sub_communities = list(nx.community.greedy_modularity_communities(subgraph))
            if len(sub_communities) > 1:
                return [set(comm) for comm in sub_communities if len(comm) >= 2]
        except:
            pass

        # Fallback: split by connected components or randomly
        components = list(nx.connected_components(subgraph.to_undirected()))
        if len(components) > 1:
            return [set(comp) for comp in components]

        # Last resort: split roughly in half
        cluster_list = list(large_cluster)
        mid = len(cluster_list) // 2
        return [set(cluster_list[:mid]), set(cluster_list[mid:])]

    def _merge_small_clusters(self, graph, small_clusters, min_cluster_size):
        """
        Merge small clusters and singletons based on connectivity.
        """
        if not small_clusters:
            return []

        merged_clusters = []
        remaining = small_clusters.copy()

        while remaining:
            current_cluster = set(remaining.pop(0))

            # Try to merge with other small clusters that have connections
            merged_any = True
            while merged_any and len(current_cluster) < min_cluster_size * 3:
                merged_any = False

                for i, other_cluster in enumerate(remaining):
                    other_set = set(other_cluster)

                    # Check if there are edges between clusters
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

    def _is_good_clustering(self, communities, target_clusters, min_cluster_size, total_nodes):
        """
        Evaluate if a clustering result is good enough.
        """
        if not communities:
            return False

        # Filter out clusters that are too small
        valid_clusters = [c for c in communities if len(c) >= min_cluster_size]

        if len(valid_clusters) == 0:
            return False

        # Check if we're in a reasonable range of target clusters
        cluster_count = len(valid_clusters)
        if cluster_count < max(2, target_clusters // 6):  # At least 1/6 of target (was 1/4)
            return False

        if cluster_count > target_clusters * 2:  # Not more than 2x target
            return False

        # Check coverage - what percentage of nodes are in valid clusters
        covered_nodes = sum(len(c) for c in valid_clusters)
        coverage = covered_nodes / total_nodes if total_nodes > 0 else 0

        if coverage < 0.4:  # At least 40% of nodes should be clustered (was 60%)
            return False

        # Check for reasonable size distribution (avoid too many singletons or giant clusters)
        singleton_count = sum(1 for c in communities if len(c) == 1)
        if singleton_count > total_nodes * 0.8:  # More than 80% singletons is bad (was 60%)
            return False

        # Check for giant clusters that dominate - be more lenient for small graphs
        largest_cluster_size = max(len(c) for c in valid_clusters)
        max_cluster_ratio = 0.6 if total_nodes < 50 else 0.4  # Allow larger clusters for small graphs
        if largest_cluster_size > total_nodes * max_cluster_ratio:
            return False

        # Check cluster size variance - prefer more balanced clusters, but be more lenient
        cluster_sizes = [len(c) for c in valid_clusters]
        avg_size = sum(cluster_sizes) / len(cluster_sizes)
        max_size = max(cluster_sizes)

        # If largest cluster is more than 8x the average, it's probably too unbalanced (was 5x)
        if max_size > avg_size * 8:
            return False

        logger.info(
            f"Good clustering found: {cluster_count} clusters, {coverage:.2%} coverage, {singleton_count} singletons, largest: {largest_cluster_size}")
        return True

    @staticmethod
    def __cluster_str(communities: list, cfg_graph_x: nx.DiGraph) -> str:
        # Filter and sort communities by size
        valid_communities = [c for c in communities if len(c) >= 2]  # Only show clusters with 2+ nodes
        top_communities = sorted(valid_communities, key=len, reverse=True)

        # Limit to reasonable number for display
        display_communities = top_communities[:25]  # Show up to 25 clusters

        communities_str = f"Cluster Definitions ({len(display_communities)} clusters shown):\n\n"
        for idx, community in enumerate(display_communities, start=1):
            community_list = sorted(list(community))
            communities_str += f"Cluster {idx} ({len(community)} nodes): {community_list}\n\n"

        cluster_to_cluster_calls: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
        node_to_cluster = {node: idx for idx, community in enumerate(display_communities) for node in community}

        for src, dst in cfg_graph_x.edges():
            src_cluster = node_to_cluster.get(src)
            dst_cluster = node_to_cluster.get(dst)

            # Skip edges where either node doesn't belong to any cluster
            if src_cluster is None or dst_cluster is None:
                continue
            if src_cluster != dst_cluster:
                cluster_to_cluster_calls[src_cluster][dst_cluster].append(f"{src} → {dst}")

        inter_cluster_str = "Inter-Cluster Connections:\n\n"
        if cluster_to_cluster_calls:
            for src_cluster_id in sorted(cluster_to_cluster_calls.keys()):
                for dst_cluster_id in sorted(cluster_to_cluster_calls[src_cluster_id].keys()):
                    calls = cluster_to_cluster_calls[src_cluster_id][dst_cluster_id]
                    # Display indices are 1-based
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
    def __non_cluster_str(graph_x, top_nodes) -> str:
        # Add Non-clustered edges
        non_cluster_edges = []
        for src, dst in graph_x.edges():
            # Edge is non-clustered if at least one node is outside top clusters
            if src not in top_nodes or dst not in top_nodes:
                non_cluster_edges.append((src, dst))

        other_edges_str = ""
        if non_cluster_edges:
            other_edges_str = "Outside of the main clusters we also have communication between:\n\n"
            for src, dst in sorted(non_cluster_edges):
                other_edges_str += f"  - {src} calls {dst}\n"
            other_edges_str += "\n"
        return other_edges_str

    def __str__(self):
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges\n"
        for _, node in self.nodes.items():
            if node.methods_called_by_me:
                result += f"Method {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}\n"
        return result

    def llm_str(self, size_limit=2_500_000, skip_nodes=[]):
        """
        Return a string representation with size limits.
        If output exceeds size_limit, group method calls by class.
        """
        # First try the default string representation
        default_str = str(self)

        logger.info(f"[CFG Tool] LLM string: {len(default_str)} characters, size limit: {size_limit} characters")

        if len(default_str) <= size_limit:
            return default_str

        # Group method nodes by class for type 6 (methods)
        class_calls: dict[str, dict[str, int]] = {}
        function_calls: list[str] = []

        logger.info(
            f"[CallGraph] Control flow graph is too large, grouping method calls by class. ({len(default_str)} characters)"
        )

        for _, node in self.nodes.items():
            if node in skip_nodes:
                continue
            if node.type == 6 and node.methods_called_by_me:  # type 6 = method
                # Extract class name from fully qualified name
                parts = node.fully_qualified_name.split(".")
                if len(parts) > 1:
                    class_name = ".".join(parts[:-1])  # Everything except method name

                    if class_name not in class_calls:
                        class_calls[class_name] = {}

                    # Group called methods by their classes
                    for called_method in node.methods_called_by_me:
                        called_parts = called_method.split(".")
                        if len(called_parts) > 1:
                            called_class = ".".join(called_parts[:-1])
                            if called_class not in class_calls[class_name]:
                                class_calls[class_name][called_class] = 0
                            class_calls[class_name][called_class] += 1
                        else:
                            # Handle cases where called method doesn't have class structure
                            if called_method not in class_calls[class_name]:
                                class_calls[class_name][called_method] = 0
                            class_calls[class_name][called_method] += 1
                else:
                    # This is a function (not a class method), keep original format
                    function_calls.append(
                        f"Function {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}"
                    )
            elif node.methods_called_by_me:
                # Non-method nodes that have calls, keep original format
                function_calls.append(
                    f"Function {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}"
                )

        # Build the grouped output
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges (grouped view)\n"

        # Add grouped class calls
        for class_name, called_classes in class_calls.items():
            calls_str = []
            for called_class, count in called_classes.items():
                calls_str.append(f"{called_class}({count} methods)")

            if calls_str:
                result += f"Class {class_name} is calling the following classes {', '.join(calls_str)}\n"

        # Add function calls in original format
        for func_call in function_calls:
            result += func_call + "\n"

        logger.info(f"[CallGraph] Control flow graph grouped by class, total characters: {len(result)}")
        return result
