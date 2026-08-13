import unittest

import networkx as nx

from static_analyzer.cfg import CallGraph, Edge
from static_analyzer.constants import NodeType
from static_analyzer.node import Node


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

        edge = Edge(src, dst, [])

        self.assertEqual(edge.src_node, src)
        self.assertEqual(edge.dst_node, dst)

    def test_get_source(self):
        # Test getting source node name
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        edge = Edge(src, dst, [])

        self.assertEqual(edge.get_source(), "module.src")

    def test_get_destination(self):
        # Test getting destination node name
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        edge = Edge(src, dst, [])

        self.assertEqual(edge.get_destination(), "module.dst")

    def test_edge_repr(self):
        # Test string representation
        src = Node("module.src", 12, "/file.py", 1, 10)
        dst = Node("module.dst", 12, "/file.py", 20, 30)

        edge = Edge(src, dst, [])
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
        self.assertEqual(len(graph._edge_by_key), 0)

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
        self.assertIn(("module.src", "module.dst"), graph._edge_by_key)
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
        self.assertEqual(len(graph._edge_by_key), 1)

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

    def test_filter_by_files_creates_new_callgraph(self):
        graph = CallGraph()
        for i in range(10):
            graph.add_node(Node(f"module.func{i}", 12, f"/file{i % 2}.py", i * 10, i * 10 + 5))
        for i in range(9):
            graph.add_edge(f"module.func{i}", f"module.func{i+1}")

        sub_graph = graph.filter_by_files({"/file0.py"})

        self.assertIsInstance(sub_graph, CallGraph)
        self.assertIsNot(sub_graph, graph)
        self.assertLess(len(sub_graph.nodes), len(graph.nodes))
        self.assertTrue(all(n.file_path == "/file0.py" for n in sub_graph.nodes.values()))

    def test_filter_by_files_empty_set(self):
        graph = CallGraph()
        graph.add_node(Node("module.func", 12, "/file.py", 1, 10))

        sub_graph = graph.filter_by_files(set())

        self.assertEqual(len(sub_graph.nodes), 0)
        self.assertEqual(len(sub_graph.edges), 0)

    def test_filter_by_files_keeps_only_internal_edges(self):
        graph = CallGraph()
        graph.add_node(Node("module.func1", 12, "/file.py", 1, 10))
        graph.add_node(Node("module.func2", 12, "/file.py", 20, 30))
        graph.add_node(Node("module.func3", 12, "/other.py", 1, 10))
        graph.add_edge("module.func1", "module.func2")
        graph.add_edge("module.func2", "module.func3")

        sub_graph = graph.filter_by_files({"/file.py"})

        self.assertEqual(len(sub_graph.edges), 1)
        for edge in sub_graph.edges:
            self.assertIn(edge.get_source(), sub_graph.nodes)
            self.assertIn(edge.get_destination(), sub_graph.nodes)

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

        # Edge key lookup must have been rewritten so dedup still works
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
