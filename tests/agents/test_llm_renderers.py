import json
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, Relation
from agents.llm_renderers import render_call_graph, render_scope_context
from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.clustering import ClusterConnectionEdge, ClusterGroup, ClusterScopeResult, GroupConnection
from static_analyzer.config import NodeType
from static_analyzer.node import Node


class TestRenderCallGraph(unittest.TestCase):
    def test_small_graph_stays_detailed(self):
        graph = CallGraph()
        graph.add_node(Node("module.src", NodeType.FUNCTION, "/file.py", 1, 10))
        graph.add_node(Node("module.dst", NodeType.FUNCTION, "/file.py", 20, 30))
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
        graph.add_node(Node("module.function1", NodeType.FUNCTION, "/file.py", 1, 10))
        graph.add_node(Node("module.function2", NodeType.FUNCTION, "/file.py", 20, 30))
        graph.add_edge("module.function1", "module.function2")

        result = render_call_graph(graph, size_limit=10)

        self.assertIn("class-level summary", result)
        self.assertIn("Function module.function1 calls: module.function2", result)
        self.assertNotIn("Class ", result)

    def test_skip_nodes_are_excluded_from_the_header_count(self):
        graph = CallGraph()
        node1 = Node("module.func1", NodeType.FUNCTION, "/file.py", 1, 10)
        node2 = Node("module.func2", NodeType.FUNCTION, "/file.py", 20, 30)
        node3 = Node("module.func3", NodeType.FUNCTION, "/file.py", 30, 40)
        for node in (node1, node2, node3):
            graph.add_node(node)
        graph.add_edge("module.func1", "module.func2")
        graph.add_edge("module.func1", "module.func3")
        graph.add_edge("module.func2", "module.func3")

        result = render_call_graph(graph, skip_nodes=[node2])

        # func1 survives on its remaining target; a skipped node leaves the count,
        # loses its own outgoing call, and is dropped from every other node's targets.
        self.assertIn("module.func1", result)
        self.assertIn("module.func3", result)
        self.assertNotIn("module.func2", result)
        self.assertIn("2 nodes", result)

    def test_source_with_only_skipped_targets_is_omitted(self):
        graph = CallGraph()
        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        node2 = Node("module.func2", 12, "/file.py", 20, 30)
        for node in (node1, node2):
            graph.add_node(node)
        graph.add_edge("module.func1", "module.func2")

        result = render_call_graph(graph, skip_nodes=[node2])

        self.assertNotIn("calls:", result)
        self.assertNotIn("module.func2", result)

    def test_unresolved_targets_survive_filtering(self):
        """External calls have no node in the graph, so they are not 'skipped'."""
        graph = CallGraph()
        node1 = Node("module.func1", 12, "/file.py", 1, 10)
        graph.add_node(node1)
        node1.methods_called_by_me.add("thirdparty.helper")

        result = render_call_graph(graph, skip_nodes=[])

        self.assertIn("thirdparty.helper", result)

    def test_skipped_targets_are_dropped_at_class_level(self):
        graph = CallGraph()
        keep = Node("pkg.ClassA.method1", NodeType.METHOD, "/a.py", 1, 10)
        skipped = Node("pkg.ClassB.method2", NodeType.METHOD, "/b.py", 1, 10)
        for node in (keep, skipped):
            graph.add_node(node)
        graph.add_edge("pkg.ClassA.method1", "pkg.ClassB.method2")

        result = render_call_graph(graph, size_limit=0, skip_nodes=[skipped])

        self.assertIn("class-level summary", result)
        self.assertNotIn("pkg.ClassB", result)


class TestRenderScopeContext(unittest.TestCase):
    def test_includes_all_group_files_boundary_reasons_and_directed_calls(self):
        graph = CallGraph(language="python")
        graph.add_node(Node("client.submit", NodeType.FUNCTION, "/repo/client.py", 10, 20))
        graph.add_node(Node("client.Payload", NodeType.CLASS, "/repo/payload.py", 1, 8))
        graph.add_node(Node("server.receive", NodeType.FUNCTION, "/repo/server.py", 30, 40))
        graph.add_edge("client.submit", "server.receive")
        graph.add_reference_edge(ReferenceEdge("client.Payload", "server.receive", EdgeKind.TYPEREF))
        scope = ClusterScopeResult(
            scope_id="root",
            graphs_by_language={"python": graph},
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1],
                    symbol_members_by_language={"python": {"client.submit", "client.Payload"}},
                    file_reasons={"/repo/client.py": "matches client terms"},
                ),
                ClusterGroup(
                    group_id="2",
                    cluster_ids=[2],
                    symbol_members_by_language={"python": {"server.receive"}},
                    file_reasons={"/repo/server.py": "matches server terms"},
                ),
            ],
            connections=[
                GroupConnection(
                    source_group_id="1",
                    target_group_id="2",
                    edges=[
                        ClusterConnectionEdge(
                            language="python",
                            source_qualified_name="client.submit",
                            target_qualified_name="server.receive",
                        )
                    ],
                )
            ],
        )
        analysis = AnalysisInsights(
            description="existing",
            components=[
                Component(name="Client", description="client", key_entities=[], component_id="1"),
                Component(name="Server", description="server", key_entities=[], component_id="2"),
            ],
            components_relations=[
                Relation(relation="submits to", src_name="Client", dst_name="Server", src_id="1", dst_id="2")
            ],
        )

        payload = json.loads(
            render_scope_context(
                scope,
                analysis,
                Path("/repo"),
                {"1"},
                {"1"},
                {"client.py"},
                incremental=True,
            )
        )

        first = payload["groups"][0]
        self.assertEqual(payload["existing_description"], "existing")
        self.assertEqual(first["status"], "changed")
        self.assertTrue(first["name_locked"])
        self.assertEqual(
            first["files"],
            [
                {"path": "client.py", "grouping_reason": "matches client terms", "changed": True},
                {
                    "path": "payload.py",
                    "grouping_reason": "member of the deterministic group",
                    "changed": False,
                },
            ],
        )
        self.assertEqual({item["path"] for item in first["bordering_files"]}, {"client.py", "payload.py"})
        self.assertEqual(
            payload["known_connections"][0],
            {
                "source_group_id": "1",
                "target_group_id": "2",
                "language": "python",
                "source_method": "client.submit",
                "source_file": "client.py",
                "source_line": 10,
                "target_method": "server.receive",
                "target_file": "server.py",
                "target_line": 30,
            },
        )
        self.assertEqual(payload["existing_relations"][0]["relation"], "submits to")
