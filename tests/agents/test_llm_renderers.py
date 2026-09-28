import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_responses import AnalysisInsights, Component, Relation
from agents.constants import ModelCapabilities
from agents.llm_errors import ContextTrimmedError, ScopeContextTooLargeError
from agents.llm_renderers.scope import (
    MAX_EXAMPLE_EDGES,
    _drop_bordering_files,
    _dump,
    _files_by_path_only,
    _one_example_without_locations,
    _tokens,
)
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
                {"path": "payload.py", "changed": False},
            ],
        )
        self.assertEqual({item["path"] for item in first["bordering_files"]}, {"client.py", "payload.py"})
        self.assertEqual(
            payload["known_connections"][0],
            {
                "source_group_id": "1",
                "target_group_id": "2",
                "calls": 1,
                "examples": [
                    {
                        "source": "client.submit",
                        "source_at": "client.py:10",
                        "target": "server.receive",
                        "target_at": "server.py:30",
                    }
                ],
            },
        )
        self.assertEqual(payload["enclosing_components"], [])
        self.assertEqual(payload["existing_relations"][0]["relation"], "submits to")

    def test_a_dense_pair_is_a_count_and_a_few_examples_not_every_edge(self):
        graph = CallGraph(language="python")
        edges = []
        for index in range(40):
            graph.add_node(Node(f"a.f{index}", NodeType.FUNCTION, f"/repo/a{index}.py", 1, 2))
            graph.add_node(Node(f"b.g{index}", NodeType.FUNCTION, f"/repo/b{index}.py", 1, 2))
            edges.append(ClusterConnectionEdge("python", f"a.f{index}", f"b.g{index}"))
        # The same edge reported twice (two call sites) counts once.
        edges.append(ClusterConnectionEdge("python", "a.f0", "b.g0"))
        scope = ClusterScopeResult(
            scope_id="root",
            graphs_by_language={"python": graph},
            groups=[
                ClusterGroup("1", [1], symbol_members_by_language={"python": {f"a.f{i}" for i in range(40)}}),
                ClusterGroup("2", [2], symbol_members_by_language={"python": {f"b.g{i}" for i in range(40)}}),
            ],
            connections=[
                GroupConnection("1", "2", edges=edges[:20]),
                GroupConnection("1", "2", edges=edges[20:]),
            ],
        )
        analysis = AnalysisInsights(description="", components=[], components_relations=[])

        payload = json.loads(
            render_scope_context(
                scope,
                analysis,
                Path("/repo"),
                {"1", "2"},
                set(),
                set(),
                incremental=False,
                enclosing_names=("Backend", "Services"),
            )
        )

        (pair,) = payload["known_connections"]
        self.assertEqual((pair["source_group_id"], pair["target_group_id"], pair["calls"]), ("1", "2", 40))
        self.assertEqual(len(pair["examples"]), MAX_EXAMPLE_EDGES)
        self.assertEqual(pair["examples"][0]["source"], "a.f0")
        self.assertEqual(payload["enclosing_components"], ["Backend", "Services"])


def _dense_scope() -> ClusterScopeResult:
    """Two groups of forty functions, every one calling across: dense enough to trim."""
    graph = CallGraph(language="python")
    edges = []
    for index in range(40):
        graph.add_node(Node(f"a.f{index}", NodeType.FUNCTION, f"/repo/a{index}.py", 1, 2))
        graph.add_node(Node(f"b.g{index}", NodeType.FUNCTION, f"/repo/b{index}.py", 1, 2))
        edges.append(ClusterConnectionEdge("python", f"a.f{index}", f"b.g{index}"))
    return ClusterScopeResult(
        scope_id="root",
        graphs_by_language={"python": graph},
        groups=[
            ClusterGroup("1", [1], symbol_members_by_language={"python": {f"a.f{i}" for i in range(40)}}),
            ClusterGroup("2", [2], symbol_members_by_language={"python": {f"b.g{i}" for i in range(40)}}),
        ],
        connections=[GroupConnection("1", "2", edges=edges)],
    )


def _render(scope: ClusterScopeResult, max_tokens: int = ModelCapabilities.FALLBACK_INPUT // 2) -> str:
    analysis = AnalysisInsights(description="", components=[], components_relations=[])
    return render_scope_context(
        scope, analysis, Path("/repo"), {"1", "2"}, set(), set(), incremental=False, max_tokens=max_tokens
    )


class TestScopeContextSize(unittest.TestCase):
    def test_path_trim_preserves_only_true_changed_markers(self):
        payload = {
            "groups": [
                {
                    "files": [
                        {"path": "unchanged.py", "changed": False, "grouping_reason": "same group"},
                        {"path": "modified.py", "changed": True, "grouping_reason": "same group"},
                        {"path": "member.py"},
                    ]
                }
            ]
        }

        _files_by_path_only(payload)

        self.assertEqual(
            payload["groups"][0]["files"], ["unchanged.py", {"path": "modified.py", "changed": True}, "member.py"]
        )

    def test_boundary_trim_preserves_reference_only_relationships(self):
        scope = _dense_scope()
        graph = scope.graphs_by_language["python"]
        graph.add_reference_edge(ReferenceEdge("a.f0", "b.g1", EdgeKind.TYPEREF))
        graph.add_reference_edge(ReferenceEdge("a.f2", "b.g3", EdgeKind.TYPEREF))
        graph.add_reference_edge(ReferenceEdge("b.g4", "a.f5", EdgeKind.INHERITS))
        graph.add_reference_edge(ReferenceEdge("a.f6", "b.g7", EdgeKind.IMPORT))
        scope.connections = []
        full = _render(scope)

        payload = json.loads(_render(scope, max_tokens=_tokens(full) - 1))

        self.assertEqual(payload["known_connections"], [])
        self.assertEqual(
            payload["groups"][0]["boundary_references"],
            ["import reference to group 2", "inherits reference from group 2", "typeref reference to group 2"],
        )
        self.assertEqual(
            payload["groups"][1]["boundary_references"],
            ["import reference from group 1", "inherits reference to group 1", "typeref reference from group 1"],
        )
        self.assertNotIn("bordering_files", payload["groups"][0])

    def test_a_full_run_sends_compact_json_without_repeated_defaults(self):
        """Why: indentation was a third of the payload, and on a large repo most files carry the same
        default reason and a ``changed: false`` that means nothing outside incremental mode."""
        text = _render(_dense_scope())
        payload = json.loads(text)

        self.assertNotIn("\n", text)
        self.assertEqual(payload["groups"][0]["files"][0], {"path": "a0.py"})

    @patch("agents.llm_renderers.scope.capture_error")
    def test_a_render_within_the_default_budget_keeps_everything(self, capture_error):
        payload = json.loads(_render(_dense_scope()))

        self.assertTrue(payload["groups"][0]["bordering_files"])
        self.assertEqual(len(payload["known_connections"][0]["examples"]), MAX_EXAMPLE_EDGES)
        capture_error.assert_not_called()

    def test_over_budget_drops_bordering_files_first(self):
        full = _render(_dense_scope())

        payload = json.loads(_render(_dense_scope(), max_tokens=_tokens(full) - 1))

        self.assertNotIn("bordering_files", payload["groups"][0])
        self.assertEqual(len(payload["known_connections"][0]["examples"]), MAX_EXAMPLE_EDGES)
        self.assertEqual(payload["groups"][0]["files"][0], {"path": "a0.py"})

    @patch("agents.llm_renderers.scope.capture_error")
    def test_further_over_keeps_one_example_then_bare_paths(self, capture_error):
        trimmed = json.loads(_render(_dense_scope()))
        _drop_bordering_files(trimmed)
        after_first = _tokens(_dump(trimmed))
        _one_example_without_locations(trimmed)
        after_second = _tokens(_dump(trimmed))

        second = json.loads(_render(_dense_scope(), max_tokens=after_first - 1))
        capture_error.reset_mock()
        third = json.loads(_render(_dense_scope(), max_tokens=after_second - 1))

        self.assertEqual(second["known_connections"][0]["examples"], [{"source": "a.f0", "target": "b.g0"}])
        self.assertIsInstance(second["groups"][0]["files"][0], dict)
        self.assertEqual(third["groups"][0]["files"][0], "a0.py")
        capture_error.assert_called_once()
        command, error = capture_error.call_args.args
        self.assertEqual(command, "scope_analysis")
        self.assertIsInstance(error, ContextTrimmedError)
        properties = capture_error.call_args.kwargs["extra"]
        self.assertEqual(properties["error_type"], "context_trimmed")
        self.assertTrue(properties["nonfatal"])
        self.assertEqual(properties["scope_id"], "root")
        self.assertEqual(properties["allowed_tokens"], after_second - 1)
        self.assertGreater(properties["original_tokens"], properties["allowed_tokens"])
        self.assertLessEqual(properties["trimmed_tokens"], properties["allowed_tokens"])
        self.assertEqual(
            properties["trim_steps"],
            [
                "compacting boundary evidence",
                "keeping one example per connection, without locations",
                "listing files by path only",
            ],
        )

    @patch("agents.llm_renderers.scope.capture_error")
    def test_a_scope_that_cannot_fit_is_refused_not_sent(self, capture_error):
        """Why: sent anyway, the provider rejects it with a 400 that OpenRouter wraps around an upstream
        401 — which is how oversized prompts reached users as "your API key was rejected"."""
        with self.assertRaises(ScopeContextTooLargeError) as ctx:
            _render(_dense_scope(), max_tokens=10)

        self.assertEqual(ctx.exception.scope_id, "root")
        self.assertEqual(ctx.exception.allowed_tokens, 10)
        self.assertIn("larger context window", str(ctx.exception))
        capture_error.assert_not_called()
