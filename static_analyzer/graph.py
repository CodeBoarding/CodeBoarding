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

        communities = list(nx.community.greedy_modularity_communities(cfg_graph_x))
        if not communities:
            summary = "No significant clusters found."
            logger.info(summary)
            return summary

        top_communities = sorted(communities, key=len, reverse=True)[:10]
        # Each of the top communities has to have at least 5% of the nodes
        min_nodes = max(2, int(0.05 * cfg_graph_x.number_of_nodes()))
        top_communities = [c for c in top_communities if len(c) >= min_nodes]
        top_nodes = set().union(*communities) if communities else set()

        cluster_str = self.__cluster_str(top_communities, cfg_graph_x)
        non_cluster_str = self.__non_cluster_str(cfg_graph_x, top_nodes)

        return cluster_str + non_cluster_str

    @staticmethod
    def __cluster_str(top_communities: list, cfg_graph_x: nx.DiGraph) -> str:
        communities_str = "Cluster Definitions:\n\n"
        for idx, community in enumerate(top_communities, start=1):
            communities_str += f"Cluster {idx}: {sorted(community)}\n\n"

        cluster_to_cluster_calls = defaultdict(lambda: defaultdict(list))
        node_to_cluster = {node: idx for idx, community in enumerate(top_communities) for node in community}

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
                    # Map cluster indices back to 1-based display indices
                    src_display = next(
                        (
                            i
                            for i, c in enumerate(top_communities, 1)
                            if any(n for n in c if node_to_cluster.get(n) == src_cluster_id)
                        ),
                        src_cluster_id,
                    )
                    dst_display = next(
                        (
                            i
                            for i, c in enumerate(top_communities, 1)
                            if any(n for n in c if node_to_cluster.get(n) == dst_cluster_id)
                        ),
                        dst_cluster_id,
                    )

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
        class_calls = {}
        function_calls = []

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
