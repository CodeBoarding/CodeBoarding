import unittest

from agents.llm_renderers import render_call_graph
from static_analyzer.cfg import CallGraph
from static_analyzer.constants import NodeType
from static_analyzer.node import Node


class TestRenderCallGraph(unittest.TestCase):
    def test_small_graph_stays_detailed(self):
        graph = CallGraph()
        graph.add_node(Node("module.src", 12, "/file.py", 1, 10))
        graph.add_node(Node("module.dst", 12, "/file.py", 20, 30))
        graph.add_edge("module.src", "module.dst")

        result = render_call_graph(graph, size_limit=10000)

        self.assertIn("module.src", result)
        self.assertIn("module.dst", result)
        self.assertIn("calls:", result)
        self.assertNotIn("class-level summary", result)

    def test_large_graph_falls_back_to_class_level(self):
        graph = CallGraph()
        for i in range(50):
            graph.add_node(Node(f"class{i % 5}.ClassA.method{i}", NodeType.METHOD, "/file.py", i * 10, i * 10 + 5))
        for i in range(49):
            graph.add_edge(f"class{i % 5}.ClassA.method{i}", f"class{(i+1) % 5}.ClassA.method{i+1}")

        result = render_call_graph(graph, size_limit=100)

        self.assertIn("class-level summary", result)
        self.assertIn("Class", result)

    def test_functions_render_as_functions_at_class_level(self):
        graph = CallGraph()
        graph.add_node(Node("module.function1", 12, "/file.py", 1, 10))
        graph.add_node(Node("module.function2", 12, "/file.py", 20, 30))
        graph.add_edge("module.function1", "module.function2")

        result = render_call_graph(graph, size_limit=100)

        self.assertIn("Function", result)

    def test_skip_nodes_are_excluded_from_the_header_count(self):
        graph = CallGraph()
        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        node2 = Node("module.func2", 12, "/file.py", 20, 30)
        node3 = Node("module.func3", 12, "/file.py", 30, 40)
        for node in (node1, node2, node3):
            graph.add_node(node)
        graph.add_edge("module.func1", "module.func2")
        graph.add_edge("module.func2", "module.func3")

        # func1 still lists func2 as a target (it lives in methods_called_by_me),
        # but func2's own outgoing call is suppressed and it leaves the node count.
        result = render_call_graph(graph, skip_nodes=[node2])

        self.assertIn("module.func1", result)
        self.assertIn("module.func2", result)
        self.assertIn("2 nodes", result)
