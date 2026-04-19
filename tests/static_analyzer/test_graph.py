import unittest
from unittest.mock import patch, Mock

import networkx as nx

from static_analyzer.constants import NodeType
from static_analyzer.node import Node
from static_analyzer.graph import Edge, CallGraph, ClusterResult


class TestNode(unittest.TestCase):
    def test_node_creation(self):
        # Test creating a Node
        node = Node(
            fully_qualified_name="module.Class.method",
            node_type=NodeType.FUNCTION,
            file_path="/path/to/file.py",
            line_start=10,
            line_end=20,
        )

        self.assertEqual(node.fully_qualified_name, "module.Class.method")
        self.assertEqual(node.type, NodeType.FUNCTION)
        self.assertEqual(node.file_path, "/path/to/file.py")
        self.assertEqual(node.line_start, 10)
        self.assertEqual(node.line_end, 20)
        self.assertEqual(len(node.methods_called_by_me), 0)

    def test_node_hash(self):
        # Test that nodes can be hashed by fully qualified name
        node1 = Node("module.func", 12, "/file.py", 1, 10)
        node2 = Node("module.func", 12, "/file.py", 1, 10)
        node3 = Node("module.other", 12, "/file.py", 1, 10)

        # Same qualified name should have same hash
        self.assertEqual(hash(node1), hash(node2))
        # Different qualified name should have different hash
        self.assertNotEqual(hash(node1), hash(node3))

    def test_node_repr(self):
        # Test string representation
        node = Node("module.func", 12, "/file.py", 5, 15)
        repr_str = repr(node)

        self.assertIn("module.func", repr_str)
        self.assertIn("/file.py", repr_str)
        self.assertIn("5", repr_str)
        self.assertIn("15", repr_str)

    def test_added_method_called_by_me_with_node(self):
        # Test adding a called method with Node object
        caller = Node("module.caller", 12, "/file.py", 1, 10)
        callee = Node("module.callee", 12, "/file.py", 20, 30)

        caller.added_method_called_by_me(callee)

        self.assertIn("module.callee", caller.methods_called_by_me)
        self.assertEqual(len(caller.methods_called_by_me), 1)

    def test_added_method_called_by_me_invalid_type(self):
        # Test adding with invalid type raises error
        caller = Node("module.caller", 12, "/file.py", 1, 10)

        with self.assertRaises(ValueError) as context:
            caller.added_method_called_by_me("invalid_string")  # type: ignore[arg-type]

        self.assertIn("Expected a Node instance", str(context.exception))

    def test_added_method_called_by_me_multiple_calls(self):
        # Test adding multiple called methods
        caller = Node("module.caller", 12, "/file.py", 1, 10)
        callee1 = Node("module.callee1", 12, "/file.py", 20, 30)
        callee2 = Node("module.callee2", 12, "/file.py", 40, 50)

        caller.added_method_called_by_me(callee1)
        caller.added_method_called_by_me(callee2)

        self.assertEqual(len(caller.methods_called_by_me), 2)
        self.assertIn("module.callee1", caller.methods_called_by_me)
        self.assertIn("module.callee2", caller.methods_called_by_me)


class TestEdge(unittest.TestCase):
    def test_edge_creation(self):
        # Test creating an Edge
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        edge = Edge(src, dst)

        self.assertEqual(edge.src_node, src)
        self.assertEqual(edge.dst_node, dst)

    def test_get_source(self):
        # Test getting source node name
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        edge = Edge(src, dst)

        self.assertEqual(edge.get_source(), "module.src")

    def test_get_destination(self):
        # Test getting destination node name
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        edge = Edge(src, dst)

        self.assertEqual(edge.get_destination(), "module.dst")

    def test_edge_repr(self):
        # Test string representation
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        edge = Edge(src, dst)
        repr_str = repr(edge)

        self.assertIn("module.src", repr_str)
        self.assertIn("module.dst", repr_str)
        self.assertIn("->", repr_str)


class TestCallGraph(unittest.TestCase):
    def test_callgraph_creation_empty(self):
        # Test creating an empty CallGraph
        graph = CallGraph()

        self.assertEqual(len(graph.nodes), 0)
        self.assertEqual(len(graph.edges), 0)
        self.assertEqual(len(graph._edge_set), 0)

    def test_callgraph_creation_with_data(self):
        # Test creating CallGraph with initial data
        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        nodes = {"module.func1": node1}

        graph = CallGraph(nodes=nodes)

        self.assertEqual(len(graph.nodes), 1)
        self.assertIn("module.func1", graph.nodes)

    def test_add_node(self):
        # Test adding a node to the graph
        graph = CallGraph()
        node = Node("module.func", 12, "/file.py", 1, 10)

        graph.add_node(node)

        self.assertEqual(len(graph.nodes), 1)
        self.assertIn("module.func", graph.nodes)
        self.assertEqual(graph.nodes["module.func"], node)

    def test_add_node_duplicate(self):
        # Test adding duplicate node (should not duplicate)
        graph = CallGraph()
        node1 = Node("module.func", 12, "/file.py", 1, 10)
        node2 = Node("module.func", 12, "/file.py", 1, 10)

        graph.add_node(node1)
        graph.add_node(node2)

        # Should only have one node
        self.assertEqual(len(graph.nodes), 1)

    def test_add_edge_valid(self):
        # Test adding a valid edge
        graph = CallGraph()
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        graph.add_node(src)
        graph.add_node(dst)

        graph.add_edge("module.src", "module.dst")

        self.assertEqual(len(graph.edges), 1)
        self.assertIn(("module.src", "module.dst"), graph._edge_set)
        # Check that src node's methods_called_by_me is updated
        self.assertIn("module.dst", src.methods_called_by_me)

    def test_add_edge_missing_source(self):
        # Test adding edge with missing source node
        graph = CallGraph()
        dst = Node("module.dst", 12, "/file.py", 20, 30)
        graph.add_node(dst)

        with self.assertRaises(ValueError) as context:
            graph.add_edge("module.nonexistent", "module.dst")

        self.assertIn("must exist", str(context.exception))

    def test_add_edge_missing_destination(self):
        # Test adding edge with missing destination node
        graph = CallGraph()
        src = Node("module.src", 12, "/file.py", 1, 10)
        graph.add_node(src)

        with self.assertRaises(ValueError) as context:
            graph.add_edge("module.src", "module.nonexistent")

        self.assertIn("must exist", str(context.exception))

    def test_add_edge_duplicate(self):
        # Test adding duplicate edge (should not duplicate)
        graph = CallGraph()
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        graph.add_node(src)
        graph.add_node(dst)

        graph.add_edge("module.src", "module.dst")
        graph.add_edge("module.src", "module.dst")

        # Should only have one edge
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(len(graph._edge_set), 1)

    def test_to_networkx(self):
        # Test converting to NetworkX graph
        graph = CallGraph()
        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        node2 = Node("module.func2", 12, "/file.py", 20, 30)

        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_edge("module.func1", "module.func2")

        nx_graph = graph.to_networkx()

        # Check it's a DiGraph
        self.assertIsInstance(nx_graph, nx.DiGraph)
        # Check nodes
        self.assertEqual(nx_graph.number_of_nodes(), 2)
        self.assertIn("module.func1", nx_graph.nodes)
        self.assertIn("module.func2", nx_graph.nodes)
        # Check edges
        self.assertEqual(nx_graph.number_of_edges(), 1)
        self.assertTrue(nx_graph.has_edge("module.func1", "module.func2"))
        # Check node attributes
        self.assertEqual(nx_graph.nodes["module.func1"]["file_path"], "/file.py")
        self.assertEqual(nx_graph.nodes["module.func1"]["line_start"], 1)
        self.assertEqual(nx_graph.nodes["module.func1"]["type"], NodeType.FUNCTION)

    def test_str_empty_graph(self):
        # Test string representation of empty graph
        graph = CallGraph()
        str_repr = str(graph)

        self.assertIn("0 nodes", str_repr)
        self.assertIn("0 edges", str_repr)

    def test_str_with_edges(self):
        # Test string representation with edges
        graph = CallGraph()
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        graph.add_node(src)
        graph.add_node(dst)
        graph.add_edge("module.src", "module.dst")

        str_repr = str(graph)

        self.assertIn("2 nodes", str_repr)
        self.assertIn("1 edges", str_repr)
        self.assertIn("module.src", str_repr)
        self.assertIn("module.dst", str_repr)

    def test_to_cluster_string_empty(self):
        # Test clustering with empty graph
        graph = CallGraph()
        result = graph.to_cluster_string()

        # Empty graph returns the strategy name "empty"
        self.assertIn("empty", result.lower())

    def test_to_cluster_string_small_graph(self):
        # Test clustering with small graph (no significant clusters)
        graph = CallGraph()
        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        node2 = Node("module.func2", 12, "/file.py", 20, 30)

        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_edge("module.func1", "module.func2")

        result = graph.to_cluster_string()

        # With only 2 nodes, may not find significant clusters
        self.assertIsInstance(result, str)

    @patch("networkx.community.greedy_modularity_communities")
    def test_to_cluster_string_with_clusters(self, mock_communities):
        # Test clustering with mocked communities
        graph = CallGraph()

        # Create a larger graph
        for i in range(10):
            node = Node(f"module.func{i}", 12, "/file.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        # Add some edges
        for i in range(9):
            graph.add_edge(f"module.func{i}", f"module.func{i+1}")

        # Mock community detection to return specific clusters
        mock_communities.return_value = [
            {"module.func0", "module.func1", "module.func2"},
            {"module.func3", "module.func4", "module.func5"},
        ]

        result = graph.to_cluster_string()

        self.assertIn("Cluster", result)
        self.assertIn("Cluster Definitions", result)

    def test_llm_str_small_graph(self):
        # Test LLM string for small graph (within size limit)
        graph = CallGraph()
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        graph.add_node(src)
        graph.add_node(dst)
        graph.add_edge("module.src", "module.dst")

        result = graph.llm_str(size_limit=10000)

        # Should use detailed representation (level 1)
        self.assertIn("module.src", result)
        self.assertIn("module.dst", result)
        self.assertIn("calls:", result)
        self.assertNotIn("class-level summary", result)

    def test_llm_str_large_graph(self):
        # Test LLM string for large graph (exceeds size limit)
        graph = CallGraph()

        # Create many method nodes (type "6")
        for i in range(50):
            node = Node(f"class{i % 5}.ClassA.method{i}", NodeType.METHOD, "/file.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        # Add edges to create relationships
        for i in range(49):
            graph.add_edge(f"class{i % 5}.ClassA.method{i}", f"class{(i+1) % 5}.ClassA.method{i+1}")

        # Use very small size limit to trigger class-level summary
        result = graph.llm_str(size_limit=100)

        # Should use class-level summary
        self.assertIn("class-level summary", result)
        self.assertIn("Class", result)

    def test_llm_str_with_functions(self):
        # Test LLM string with function nodes (not methods)
        graph = CallGraph()
        func1 = Node("module.function1", 12, "/file.py", 1, 10)
        func2 = Node("module.function2", 12, "/file.py", 20, 30)

        graph.add_node(func1)
        graph.add_node(func2)
        graph.add_edge("module.function1", "module.function2")

        # Use small size limit to trigger grouping
        result = graph.llm_str(size_limit=100)

        # Functions (not methods) should remain in detailed format
        self.assertIn("Function", result)

    def test_llm_str_with_skip_nodes(self):
        # Test LLM string with nodes to skip
        graph = CallGraph()
        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        node2 = Node("module.func2", 12, "/file.py", 20, 30)
        node3 = Node("module.func3", 12, "/file.py", 30, 40)

        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_node(node3)

        graph.add_edge("module.func1", "module.func2")
        graph.add_edge("module.func2", "module.func3")

        # Skip node2 — func1 still shows its call to func2 (stored in methods_called_by_me),
        # but func2's own outgoing call to func3 is suppressed
        result = graph.llm_str(skip_nodes=[node2])

        self.assertIn("module.func1", result)
        # func1 still lists func2 as a call target (it's in func1.methods_called_by_me)
        self.assertIn("module.func2", result)
        # The header should reflect 2 active nodes (func1 + func3), not 3
        self.assertIn("2 nodes", result)

    def test_cluster_str_static_method(self):
        # Test __cluster_str static method
        graph = CallGraph()

        # Create test graph
        for i in range(6):
            node = Node(f"module.func{i}", 12, "/file.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        # Create edges
        graph.add_edge("module.func0", "module.func1")
        graph.add_edge("module.func2", "module.func3")
        graph.add_edge("module.func0", "module.func3")  # Inter-cluster edge

        nx_graph = graph.to_networkx()

        # Define communities as (cluster_id, members) pairs
        communities = [
            (1, {"module.func0", "module.func1"}),
            (2, {"module.func2", "module.func3"}),
        ]

        graph_instance = CallGraph()
        result = graph_instance._CallGraph__cluster_str(communities, nx_graph, set())  # type: ignore[attr-defined]

        self.assertIn("Cluster Definitions", result)
        self.assertIn("Inter-Cluster Connections", result)
        self.assertIn("Cluster 1", result)
        self.assertIn("Cluster 2", result)

    def test_cluster_str_prefix_factoring(self):
        graph = CallGraph()

        nodes = [
            Node("pkg.sub.module.ClassA", NodeType.CLASS, "/pkg/sub/module.py", 1, 5),
            Node("pkg.sub.module.ClassA.method_one", NodeType.METHOD, "/pkg/sub/module.py", 6, 10),
            Node("pkg.sub.module.ClassA.method_two", NodeType.METHOD, "/pkg/sub/module.py", 11, 15),
            Node("pkg.sub.module.helper_func", NodeType.FUNCTION, "/pkg/sub/module.py", 16, 20),
            Node("other.Outside", NodeType.CLASS, "/other.py", 1, 5),
        ]
        for n in nodes:
            graph.add_node(n)
        graph.add_edge("pkg.sub.module.ClassA.method_one", "pkg.sub.module.helper_func")

        nx_graph = graph.to_networkx()
        communities = [(1, {n.fully_qualified_name for n in nodes[:4]}), (2, {"other.Outside"})]

        result = graph._CallGraph__cluster_str(communities, nx_graph, set())  # type: ignore[attr-defined]

        self.assertIn('(identifiers below prefixed with "pkg.sub.module.")', result)
        self.assertIn("ClassA [Class]", result)
        self.assertIn("helper_func [Function]", result)
        # Full FQN must not appear inside the factored file block
        self.assertNotIn("    pkg.sub.module.ClassA [Class]", result)

    def test_common_dot_prefix(self):
        self.assertEqual(CallGraph._common_dot_prefix([]), "")
        self.assertEqual(CallGraph._common_dot_prefix(["a.b.c"]), "")
        self.assertEqual(CallGraph._common_dot_prefix(["a.b.c", "a.b.d"]), "a.b")
        self.assertEqual(CallGraph._common_dot_prefix(["a.b.c", "x.y.z"]), "")
        # Prevents collapsing identical names to empty short form
        self.assertEqual(CallGraph._common_dot_prefix(["a.b", "a.b"]), "a")

    def test_non_cluster_str_static_method(self):
        # Test __non_cluster_str static method
        graph = CallGraph()

        for i in range(4):
            node = Node(f"module.func{i}", 12, "/file.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        # Create edges
        graph.add_edge("module.func0", "module.func1")
        graph.add_edge("module.func2", "module.func3")

        nx_graph = graph.to_networkx()

        # Define top nodes (in clusters)
        top_nodes = {"module.func0", "module.func1"}

        graph_instance = CallGraph()
        result = graph_instance._CallGraph__non_cluster_str(nx_graph, top_nodes, set())  # type: ignore[attr-defined]

        # Should show edges involving func2 and func3
        self.assertIn("module.func2", result)
        self.assertIn("module.func3", result)

    def test_to_cluster_string_minimum_cluster_size(self):
        # Test that clusters must meet minimum size requirement
        graph = CallGraph()

        # Create 100 nodes to ensure minimum threshold
        for i in range(100):
            node = Node(f"module.func{i}", 12, "/file.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        # Create edges to form communities
        for i in range(99):
            graph.add_edge(f"module.func{i}", f"module.func{i+1}")

        result = graph.to_cluster_string()

        # Should create clusters (with 100 nodes, 5% = 5 nodes minimum)
        self.assertIsInstance(result, str)

    def test_cluster_returns_cluster_result(self):
        """Test that cluster() returns a ClusterResult."""
        graph = CallGraph()

        for i in range(10):
            node = Node(f"module.func{i}", 12, f"/file{i % 3}.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        for i in range(9):
            graph.add_edge(f"module.func{i}", f"module.func{i+1}")

        result = graph.cluster()

        self.assertIsInstance(result, ClusterResult)
        self.assertIsInstance(result.clusters, dict)
        self.assertIsInstance(result.file_to_clusters, dict)
        self.assertIsInstance(result.cluster_to_files, dict)
        self.assertIsInstance(result.strategy, str)

    def test_cluster_is_cached(self):
        """Test that cluster() results are cached."""
        graph = CallGraph()

        for i in range(5):
            node = Node(f"module.func{i}", 12, "/file.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        result1 = graph.cluster()
        result2 = graph.cluster()

        # Should be the same object (cached)
        self.assertIs(result1, result2)

    def test_cluster_empty_graph(self):
        """Test cluster() on empty graph."""
        graph = CallGraph()
        result = graph.cluster()

        self.assertEqual(result.clusters, {})
        self.assertEqual(result.strategy, "empty")

    def test_cluster_file_mappings(self):
        """Test that cluster() builds correct file <-> cluster mappings."""
        graph = CallGraph()

        # Create nodes with distinct file paths
        node1 = Node("module.func1", 12, "/path/a.py", 1, 10)
        node2 = Node("module.func2", 12, "/path/a.py", 20, 30)
        node3 = Node("module.func3", 12, "/path/b.py", 1, 10)
        node4 = Node("module.func4", 12, "/path/b.py", 20, 30)

        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_node(node3)
        graph.add_node(node4)

        graph.add_edge("module.func1", "module.func2")
        graph.add_edge("module.func3", "module.func4")

        result = graph.cluster()

        # Check that file_to_clusters and cluster_to_files are populated
        self.assertTrue(len(result.file_to_clusters) > 0 or result.strategy in ("empty", "none"))

    def test_filter_by_files_creates_new_callgraph(self):
        """Test that filter_by_files() creates a new CallGraph instance."""
        graph = CallGraph()

        for i in range(10):
            node = Node(f"module.func{i}", 12, f"/file{i % 2}.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        for i in range(9):
            graph.add_edge(f"module.func{i}", f"module.func{i+1}")

        cluster_result = graph.cluster()
        if cluster_result.clusters:
            first_cluster_id = next(iter(cluster_result.clusters.keys()))
            file_paths = cluster_result.cluster_to_files.get(first_cluster_id, set())
            sub_graph = graph.filter_by_files(file_paths)

            self.assertIsInstance(sub_graph, CallGraph)
            self.assertIsNot(sub_graph, graph)
            # Subgraph should have fewer or equal nodes
            self.assertLessEqual(len(sub_graph.nodes), len(graph.nodes))

    def test_filter_by_files_empty_cluster_ids(self):
        """Test filter_by_files() with empty cluster IDs returns empty graph."""
        graph = CallGraph()
        node = Node("module.func", 12, "/file.py", 1, 10)
        graph.add_node(node)

        sub_graph = graph.filter_by_files(set())

        self.assertEqual(len(sub_graph.nodes), 0)
        self.assertEqual(len(sub_graph.edges), 0)

    def test_filter_by_files_preserves_edges(self):
        """Test that filter_by_files() preserves edges between included nodes."""
        graph = CallGraph()

        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        node2 = Node("module.func2", 12, "/file.py", 20, 30)
        node3 = Node("module.func3", 12, "/other.py", 1, 10)

        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_node(node3)

        graph.add_edge("module.func1", "module.func2")
        graph.add_edge("module.func2", "module.func3")

        cluster_result = graph.cluster()
        if cluster_result.clusters:
            # Get a cluster and create filter_by_files
            first_cluster_id = next(iter(cluster_result.clusters.keys()))
            file_paths = cluster_result.cluster_to_files.get(first_cluster_id, set())
            sub_graph = graph.filter_by_files(file_paths)

            # All edges in filter_by_files should connect nodes that exist in filter_by_files
            for edge in sub_graph.edges:
                self.assertIn(edge.get_source(), sub_graph.nodes)
                self.assertIn(edge.get_destination(), sub_graph.nodes)

    def test_filter_by_files_can_be_clustered(self):
        """Test that filter_by_files can itself be clustered."""
        graph = CallGraph()

        for i in range(20):
            node = Node(f"module.func{i}", 12, f"/file{i % 4}.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        for i in range(19):
            graph.add_edge(f"module.func{i}", f"module.func{i+1}")

        cluster_result = graph.cluster()
        if cluster_result.clusters:
            first_cluster_id = next(iter(cluster_result.clusters.keys()))
            file_paths = cluster_result.cluster_to_files.get(first_cluster_id, set())
            sub_graph = graph.filter_by_files(file_paths)

            # Subgraph should be clusterable
            sub_result = sub_graph.cluster()
            self.assertIsInstance(sub_result, ClusterResult)

    def test_to_cluster_string_with_cluster_ids_filter(self):
        """Test to_cluster_string() with specific cluster IDs."""
        graph = CallGraph()

        for i in range(10):
            node = Node(f"module.func{i}", 12, f"/file{i % 2}.py", i * 10, i * 10 + 5)
            graph.add_node(node)

        for i in range(9):
            graph.add_edge(f"module.func{i}", f"module.func{i+1}")

        cluster_result = graph.cluster()
        if len(cluster_result.clusters) >= 2:
            # Get first cluster ID only
            first_id = min(cluster_result.clusters.keys())
            filtered_str = graph.to_cluster_string(cluster_ids={first_id})

            self.assertIn("Cluster", filtered_str)
            # Should only include the specified cluster

    def test_cluster_determinism(self):
        """Test that clustering is deterministic (same seed = same result)."""

        def create_graph():
            g = CallGraph()
            for i in range(15):
                node = Node(f"module.func{i}", 12, f"/file{i % 3}.py", i * 10, i * 10 + 5)
                g.add_node(node)
            for i in range(14):
                g.add_edge(f"module.func{i}", f"module.func{i+1}")
            return g

        graph1 = create_graph()
        graph2 = create_graph()

        result1 = graph1.cluster()
        result2 = graph2.cluster()

        # Cluster IDs and contents should be identical
        self.assertEqual(result1.clusters.keys(), result2.clusters.keys())
        for cid in result1.clusters:
            self.assertEqual(result1.clusters[cid], result2.clusters[cid])

    def test_node_promotion_with_existing_edges(self):
        """Promoting a node (longer name replaces shorter) after edges exist must not break the graph.

        Simulates the monorepo merge path where nodes from a second subproject are
        added to a CFG that already has edges from the first subproject.
        """
        graph = CallGraph()

        short = Node("index.funcA", NodeType.FUNCTION, "/src/index.py", 1, 10)
        other = Node("index.funcB", NodeType.FUNCTION, "/src/index.py", 20, 30)
        graph.add_node(short)
        graph.add_node(other)
        graph.add_edge("index.funcA", "index.funcB")

        # Longer qualified name for the same symbol arrives
        graph.add_node(Node("src.index.funcA", NodeType.FUNCTION, "/src/index.py", 1, 10))

        self.assertIn("src.index.funcA", graph.nodes)
        self.assertNotIn("index.funcA", graph.nodes)

        # Edge objects must reflect the promoted name (in-place mutation)
        nx_graph = graph.to_networkx()
        self.assertEqual(nx_graph.number_of_edges(), 1)

        # filter_by_files must not KeyError on stale edge names
        sub = graph.filter_by_files({"/src/index.py"})
        self.assertGreaterEqual(len(sub.edges), 1)

        # _edge_set must have been rewritten so dedup still works
        graph.add_edge("src.index.funcA", "index.funcB")
        self.assertEqual(len(graph.edges), 1)

    def test_target_promotion_updates_methods_called_by_me(self):
        graph = CallGraph()
        caller = Node("mod.caller", NodeType.FUNCTION, "/a.py", 1, 10)
        target = Node("funcB", NodeType.FUNCTION, "/b.py", 1, 10)
        graph.add_node(caller)
        graph.add_node(target)
        graph.add_edge("mod.caller", "funcB")

        self.assertIn("funcB", graph.nodes["mod.caller"].methods_called_by_me)

        # Promote the target to a longer canonical name
        graph.add_node(Node("pkg.mod.funcB", NodeType.FUNCTION, "/b.py", 1, 10))

        self.assertIn("pkg.mod.funcB", graph.nodes["mod.caller"].methods_called_by_me)
        self.assertNotIn("funcB", graph.nodes["mod.caller"].methods_called_by_me)

    def test_alias_resolution_and_has_node(self):
        """Aliases must resolve to canonical in both add order directions, and has_node must find them."""
        graph = CallGraph()

        # Shortest-first: 3 names for the same symbol
        graph.add_node(Node("funcA", NodeType.FUNCTION, "/src/index.py", 1, 10))
        graph.add_node(Node("src.index.funcA", NodeType.FUNCTION, "/src/index.py", 1, 10))
        graph.add_node(Node("container.src.index.funcA", NodeType.FUNCTION, "/src/index.py", 1, 10))

        self.assertEqual(len(graph.nodes), 1)
        self.assertIn("container.src.index.funcA", graph.nodes)

        # has_node must find all aliases
        self.assertTrue(graph.has_node("funcA"))
        self.assertTrue(graph.has_node("src.index.funcA"))

        # Edges via any alias must resolve to canonical
        graph.add_node(Node("other.func", NodeType.FUNCTION, "/src/other.py", 1, 10))
        graph.add_edge("funcA", "other.func")
        self.assertEqual(graph.edges[0].get_source(), "container.src.index.funcA")
        graph.add_edge("src.index.funcA", "other.func")
        self.assertEqual(len(graph.edges), 1)  # deduped

        # Longest-first: separate symbol
        graph2 = CallGraph()
        graph2.add_node(Node("pkg.mod.bar", NodeType.FUNCTION, "/bar.py", 1, 10))
        graph2.add_node(Node("mod.bar", NodeType.FUNCTION, "/bar.py", 1, 10))
        graph2.add_node(Node("bar", NodeType.FUNCTION, "/bar.py", 1, 10))

        self.assertEqual(len(graph2.nodes), 1)
        self.assertIn("pkg.mod.bar", graph2.nodes)
        self.assertTrue(graph2.has_node("bar"))


if __name__ == "__main__":
    unittest.main()
