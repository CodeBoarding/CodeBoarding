"""Tests for the in-memory CFG-update helpers used by the warm-start flow.

The warm-start flow loads a prior pkl, asks git for files changed since the
pkl's tag SHA, and uses ``invalidate_files`` + ``merge_results`` to bring
the cached analysis-dict up to date in memory before saving a new pkl.
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.analysis_cache import StaticAnalysisCache, invalidate_files, merge_results
from static_analyzer.analysis_result import AnalysisData, CallSiteLocation, InvalidatedEdge, StaticAnalysisResults
from static_analyzer.config import Language, NodeType
from static_analyzer.cfg import CallGraph
from static_analyzer.node import Node
from static_analyzer.graph_definitions import GraphIndex, definition_nodes
from static_analyzer.incremental_orchestrator import (
    _call_shapes,
    _add_outbound_edges_from_changed_files,
    _restore_inbound_edges_via_definitions,
    update_cfg_for_changed_files,
)
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter
from static_analyzer.engine.source_inspector import SourceInspector
from utils import CODEBOARDING_DIR_NAME


def _node(qname: str, file_path: str, line_start: int = 1) -> Node:
    return Node(
        fully_qualified_name=qname,
        node_type=NodeType.FUNCTION,
        file_path=file_path,
        line_start=line_start,
        line_end=line_start + 1,
    )


def _result(
    cg: CallGraph,
    references: list[Node] | None = None,
    source_files: list[str] | None = None,
    class_hierarchies: dict | None = None,
    package_relations: dict | None = None,
) -> dict:
    return {
        "call_graph": cg,
        "class_hierarchies": class_hierarchies or {},
        "package_relations": package_relations or {},
        "references": references or [],
        "source_files": [Path(p) for p in (source_files or [])],
    }


def _analysis_data(result: dict) -> AnalysisData:
    return AnalysisData.from_dict(result)


class TestInvalidateFiles(unittest.TestCase):
    def test_drops_nodes_from_changed_files(self) -> None:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cached = _result(cg, source_files=["a.py", "b.py"])

        updated = invalidate_files(cached, {Path("a.py")}).analysis

        self.assertNotIn("a.foo", updated.call_graph.nodes)
        self.assertIn("b.bar", updated.call_graph.nodes)
        self.assertEqual([str(p) for p in updated.source_files], ["b.py"])

    def test_cascades_edges_when_endpoint_dropped(self) -> None:
        # Edge a.foo -> b.bar must be dropped when a.foo is removed; the
        # remaining b.bar must stay and the dangling-edge guard must not fire.
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cg.add_edge("a.foo", "b.bar")
        cached = _result(cg, source_files=["a.py", "b.py"])

        updated = invalidate_files(cached, {Path("a.py")}).analysis

        self.assertEqual(len(updated.call_graph.edges), 0)

    def test_tracks_invalidated_cross_boundary_edges_for_merge(self) -> None:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cg.add_edge("a.foo", "b.bar")
        cached = _result(cg, source_files=["a.py", "b.py"])

        updated = invalidate_files(cached, {Path("a.py")})

        self.assertEqual(updated.invalidated_files, {"a.py"})
        self.assertEqual(
            [(src, dst) for src, dst, *_ in updated.invalidated_edges],
            [("a.foo", "b.bar")],
        )

    def test_drops_references_class_hierarchies_and_packages(self) -> None:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cached = _result(
            cg,
            references=[_node("a.foo", "a.py"), _node("b.bar", "b.py")],
            source_files=["a.py", "b.py"],
            class_hierarchies={
                "A": {"file_path": "a.py", "superclasses": [], "subclasses": []},
                "B": {"file_path": "b.py", "superclasses": [], "subclasses": []},
            },
            package_relations={"pkg": {"files": ["a.py", "b.py"]}},
        )

        updated = invalidate_files(cached, {Path("a.py")}).analysis

        self.assertEqual([r.fully_qualified_name for r in updated.references], ["b.bar"])
        self.assertEqual(set(updated.class_hierarchies.keys()), {"B"})
        self.assertEqual(updated.package_relations["pkg"]["files"], ["b.py"])

    def test_diagnostics_preserved_for_unchanged_files(self) -> None:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cached = _result(cg, source_files=["a.py", "b.py"])
        cached["diagnostics"] = {"a.py": ["d1"], "b.py": ["d2"]}

        updated = invalidate_files(cached, {Path("a.py")}).analysis

        self.assertEqual(updated.diagnostics, {"b.py": ["d2"]})


class TestMergeResults(unittest.TestCase):
    def test_unions_disjoint_call_graphs(self) -> None:
        cached_cg = CallGraph(language="python")
        cached_cg.add_node(_node("a.foo", "a.py"))
        new_cg = CallGraph(language="python")
        new_cg.add_node(_node("b.bar", "b.py"))

        merged = merge_results(
            _analysis_data(_result(cached_cg, source_files=["a.py"])), _result(new_cg, source_files=["b.py"])
        )

        self.assertEqual(set(merged.call_graph.nodes), {"a.foo", "b.bar"})

    def test_new_overrides_cached_for_same_file_references(self) -> None:
        # ``b.bar`` lives in b.py in both halves; the new half wins.
        cached = _result(
            CallGraph(language="python"),
            references=[_node("b.bar", "b.py", line_start=10)],
            source_files=["b.py"],
        )
        new = _result(
            CallGraph(language="python"),
            references=[_node("b.bar", "b.py", line_start=20)],
            source_files=["b.py"],
        )

        merged = merge_results(_analysis_data(cached), new)

        self.assertEqual([(r.fully_qualified_name, r.line_start) for r in merged.references], [("b.bar", 20)])

    def test_cached_references_for_files_not_in_new_are_kept(self) -> None:
        cached = _result(
            CallGraph(language="python"),
            references=[_node("a.foo", "a.py"), _node("b.bar", "b.py")],
            source_files=["a.py", "b.py"],
        )
        new = _result(
            CallGraph(language="python"),
            references=[_node("b.bar", "b.py", line_start=99)],
            source_files=["b.py"],
        )

        merged = merge_results(_analysis_data(cached), new)

        names = sorted((r.fully_qualified_name, r.line_start) for r in merged.references)
        self.assertEqual(names, [("a.foo", 1), ("b.bar", 99)])

    def test_diagnostics_merge_with_new_winning(self) -> None:
        cached = _result(CallGraph(language="python"), source_files=["a.py", "b.py"])
        cached["diagnostics"] = {"a.py": ["old-a"], "b.py": ["old-b"]}
        new = _result(CallGraph(language="python"), source_files=["b.py"])
        new["diagnostics"] = {"b.py": ["new-b"]}

        merged = merge_results(_analysis_data(cached), new)

        self.assertEqual(merged.diagnostics, {"a.py": ["old-a"], "b.py": ["new-b"]})


class TestWarmStartDeletion(unittest.TestCase):
    def test_deleted_changed_file_is_removed_from_cached_cfg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            live_file = project_path / "b.py"
            live_file.write_text("def bar():\n    pass\n", encoding="utf-8")
            deleted_file = project_path / "a.py"

            cg = CallGraph(language="python")
            cg.add_node(_node("a.foo", str(deleted_file)))
            cg.add_node(_node("b.bar", str(live_file)))
            cached = _result(
                cg,
                references=[_node("a.foo", str(deleted_file)), _node("b.bar", str(live_file))],
                source_files=[str(deleted_file), str(live_file)],
            )

            adapter = MagicMock()
            adapter.file_extensions = [".py"]
            adapter.language = "python"
            engine_client = MagicMock()
            engine_client.get_collected_diagnostics.return_value = {}
            ignore_manager = MagicMock()
            ignore_manager.should_ignore.return_value = False

            updated = update_cfg_for_changed_files(
                cached,
                {deleted_file},
                adapter,
                project_path,
                project_path,
                engine_client,
                ignore_manager,
            )

            self.assertNotIn("a.foo", updated["call_graph"].nodes)
            self.assertIn("b.bar", updated["call_graph"].nodes)
            self.assertEqual([str(path) for path in updated["source_files"]], [str(live_file)])


class TestWarmStartOutboundEdges(unittest.TestCase):
    def test_a_link_result_names_the_declaration_its_whole_range_does_not(self) -> None:
        """``linkSupport`` is what makes this exact: a bare Location starts at ``function``."""
        file_path = Path("/repo/unchanged.php")
        call_graph = CallGraph(language="php")
        call_graph.add_node(
            Node(
                fully_qualified_name="unchanged.unchanged_target",
                node_type=NodeType.FUNCTION,
                file_path=str(file_path),
                line_start=3,
                line_end=3,
                col_start=9,
            )
        )
        index = GraphIndex(call_graph, SourceInspector())
        link = {
            "targetUri": file_path.as_uri(),
            "targetSelectionRange": {"start": {"line": 2, "character": 9}, "end": {"line": 2, "character": 24}},
        }
        whole_declaration = {
            "uri": file_path.as_uri(),
            "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 66}},
        }

        self.assertEqual(
            [node.fully_qualified_name for node in definition_nodes(index, link)], ["unchanged.unchanged_target"]
        )
        self.assertEqual(definition_nodes(index, whole_declaration), [])

    def test_definition_resolution_includes_the_most_specific_node_and_its_class(self) -> None:
        file_path = Path("/repo/pkg/converter.py")
        call_graph = CallGraph(language="python")
        call_graph.add_node(
            Node(
                fully_qualified_name="pkg.converter.DocumentConverter",
                node_type=NodeType.CLASS,
                file_path=str(file_path),
                line_start=1,
                line_end=40,
            )
        )
        call_graph.add_node(
            Node(
                fully_qualified_name="pkg.converter.DocumentConverter.convert",
                node_type=NodeType.METHOD,
                file_path=str(file_path),
                line_start=10,
                line_end=20,
                col_start=4,
            )
        )
        definition = {
            "uri": file_path.as_uri(),
            "range": {"start": {"line": 9, "character": 8}, "end": {"line": 9, "character": 15}},
        }

        matches = definition_nodes(GraphIndex(call_graph, SourceInspector()), definition, include_callable_parent=True)

        self.assertEqual(
            [node.fully_qualified_name for node in matches],
            [
                "pkg.converter.DocumentConverter.convert",
                "pkg.converter.DocumentConverter",
            ],
        )

    def test_a_position_before_every_declaration_on_the_line_names_none_of_them(self) -> None:
        file_path = Path("/repo/pkg/target.php")
        call_graph = CallGraph(language="php")
        call_graph.add_node(
            Node(
                fully_qualified_name="pkg.target.Target",
                node_type=NodeType.CLASS,
                file_path=str(file_path),
                line_start=1,
                line_end=1,
                col_start=6,
            )
        )
        call_graph.add_node(
            Node(
                fully_qualified_name="pkg.target.Target.method",
                node_type=NodeType.METHOD,
                file_path=str(file_path),
                line_start=1,
                line_end=1,
                col_start=29,
            )
        )
        definition = {
            "uri": file_path.as_uri(),
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 6}},
        }

        matches = definition_nodes(GraphIndex(call_graph, SourceInspector()), definition)

        self.assertEqual(matches, [])

    def test_definition_resolution_includes_constructor_parent_without_definition_strategy(self) -> None:
        file_path = Path("/repo/pkg/target.php")
        call_graph = CallGraph(language="php")
        call_graph.add_node(
            Node(
                fully_qualified_name="pkg.target.Target",
                node_type=NodeType.CLASS,
                file_path=str(file_path),
                line_start=1,
                line_end=5,
            )
        )
        call_graph.add_node(
            Node(
                fully_qualified_name="pkg.target.Target.__construct",
                node_type=NodeType.CONSTRUCTOR,
                file_path=str(file_path),
                line_start=2,
                line_end=2,
                col_start=5,
            )
        )
        definition = {
            "uri": file_path.as_uri(),
            "range": {"start": {"line": 1, "character": 5}, "end": {"line": 1, "character": 16}},
        }

        matches = definition_nodes(GraphIndex(call_graph, SourceInspector()), definition)

        self.assertEqual(
            [node.fully_qualified_name for node in matches],
            ["pkg.target.Target.__construct", "pkg.target.Target"],
        )


if __name__ == "__main__":
    unittest.main()


class TestWarmStartKeepsDefinitionsAnotherEngineOwns:
    def test_a_changed_caller_whose_definition_has_no_node_yet_is_handed_back(self, tmp_path: Path) -> None:
        """Solution A's changed file calls something solution B adds in the same edit; B's node is
        not in the graph when A is processed, so the site must survive until every engine merged."""
        changed = tmp_path / "Host.cs"
        changed.write_text("class Host\n{\n    void Configure() { Builder.UseAuditing(); }\n}\n")
        graph = CallGraph(language="csharp")
        graph.add_node(Node("Host", NodeType.CLASS, str(changed), line_start=1, line_end=4, col_start=0))
        graph.add_node(Node("Host.Configure()", NodeType.METHOD, str(changed), line_start=3, line_end=3, col_start=9))
        elsewhere = tmp_path / "framework" / "Builder.cs"
        client = MagicMock()
        client.send_definition_batch.side_effect = lambda queries: [
            [{"uri": elsewhere.as_uri(), "range": {"start": {"line": 20, "character": 4}}}] for _ in queries
        ]
        client.send_implementation_batch.side_effect = lambda queries: [[] for _ in queries]

        external = _add_outbound_edges_from_changed_files(
            GraphIndex(graph, SourceInspector()), [changed], client, SourceInspector(), CSharpAdapter()
        )

        assert graph.edges == []
        assert [(s.caller, s.file, s.line, s.character, s.kind) for s in external] == [
            ("Host.Configure()", str(elsewhere), 20, 4, "call")
        ]


class TestRestoringCachedEdges:
    """A cached site is re-asked as the shape it was written as, not as a plain call."""

    def _csharp_adapter(self) -> CSharpAdapter:
        return CSharpAdapter()

    def test_an_enumerated_type_is_asked_for_with_a_type_query(self, tmp_path: Path) -> None:
        """``foreach`` names a value; only a type query reaches the type it enumerates.

        Asking for its definition returns the variable, so the cached edge into the type
        can never be confirmed and the warm result silently drops it.
        """
        caller = tmp_path / "Runner.cs"
        caller.write_text(
            "class Runner\n{\n    void Run(Bag bag)\n    {\n        foreach (var item in bag) { }\n    }\n}\n"
        )
        bag = tmp_path / "Bag.cs"
        graph = CallGraph(language="csharp")
        graph.add_node(Node("Runner", NodeType.CLASS, str(caller), line_start=1, line_end=7, col_start=6))
        graph.add_node(Node("Runner.Run(Bag)", NodeType.METHOD, str(caller), line_start=3, line_end=6, col_start=9))
        graph.add_node(Node("Bag", NodeType.CLASS, str(bag), line_start=1, line_end=9, col_start=6))
        node = graph.nodes["Runner.Run(Bag)"]
        target = graph.nodes["Bag"]
        sites: list[CallSiteLocation] = [{"file": str(caller), "line": 5, "column": 30}]
        invalidated: list[InvalidatedEdge] = [("Runner.Run(Bag)", "Bag", node, target, sites)]

        client = MagicMock()
        client.send_definition_batch.side_effect = lambda queries: [[] for _ in queries]
        client.send_type_definition_batch.side_effect = lambda queries: [
            [{"uri": bag.as_uri(), "range": {"start": {"line": 0, "character": 6}}}] for _ in queries
        ]
        client.send_implementation_batch.side_effect = lambda queries: [[] for _ in queries]

        inspector = SourceInspector()
        _restore_inbound_edges_via_definitions(
            GraphIndex(graph, inspector), invalidated, set(), self._csharp_adapter(), client, inspector
        )

        assert client.send_type_definition_batch.called
        assert [(edge.get_source(), edge.get_destination()) for edge in graph.edges] == [("Runner.Run(Bag)", "Bag")]


class TestCallShapesRequests:
    """What a cached site is re-asked with, when the source says it has two shapes."""

    def _adapter(self) -> MagicMock:
        adapter = MagicMock()
        adapter.resolves_method_groups = False
        adapter.resolves_collection_initializers = False
        adapter.resolves_iterated_types = True
        return adapter

    def test_a_loop_over_a_call_is_asked_both_ways(self, tmp_path: Path) -> None:
        """The full build runs its call pass and its iteration pass over the same position.

        Asking only for the type loses the caller-to-``GetItems`` edge; asking only for the
        definition loses the enumerator edge.
        """
        source = tmp_path / "Loop.cs"
        source.write_text(
            "class A\n{\n    void Run()\n    {\n        foreach (var x in GetItems()) { }\n    }\n"
            "    int[] GetItems() => new int[0];\n}\n"
        )
        shapes = _call_shapes(source, SourceInspector(), self._adapter())

        assert shapes.requests_at((4, 26)) == [("definition", "call"), ("type_definition", "iterated")]

    def test_a_loop_over_a_variable_is_asked_only_for_its_type(self, tmp_path: Path) -> None:
        source = tmp_path / "Loop.cs"
        source.write_text("class A\n{\n    void Run(int[] bag)\n    {\n        foreach (var x in bag) { }\n    }\n}\n")
        shapes = _call_shapes(source, SourceInspector(), self._adapter())

        assert shapes.requests_at((4, 26)) == [("type_definition", "iterated")]


class TestWarmStartCallShapes:
    """The shapes the full rebuild finds must survive an edit, or a warm start loses them."""

    def _typescript_adapter(self) -> MagicMock:
        adapter = MagicMock()
        adapter.language_id = "typescript"
        adapter.resolves_method_groups = True
        adapter.resolves_collection_initializers = False
        adapter.resolves_iterated_types = False
        adapter.expands_virtual_dispatch = False
        adapter.expands_constructors = False
        return adapter

    def _run(self, graph: CallGraph, changed: Path, answers: dict[int, tuple[str, int, int]]) -> list[tuple[str, str]]:
        client = MagicMock()
        client.send_definition_batch.side_effect = lambda queries: [
            (
                [{"uri": Path(at[0]).as_uri(), "range": {"start": {"line": at[1], "character": at[2]}}}]
                if (at := answers.get(col)) is not None
                else []
            )
            for _, _line, col in queries
        ]
        client.send_implementation_batch.side_effect = lambda queries: [[] for _ in queries]
        _add_outbound_edges_from_changed_files(
            GraphIndex(graph, SourceInspector()), [changed], client, SourceInspector(), self._typescript_adapter()
        )
        return [(edge.get_source(), edge.get_destination()) for edge in graph.edges]

    def test_a_call_reaches_the_implementations_of_the_declaration_it_resolves_to(self, tmp_path: Path) -> None:
        """A full build follows every callable target with ``textDocument/implementation``.

        This adapter does not expand virtual dispatch from source, so the server's answer is
        the only route to the caller-to-implementation edge.
        """
        changed = tmp_path / "app.ts"
        changed.write_text(
            'import { Service } from "./api";\n\nexport function run(s: Service) {\n    s.handle();\n}\n'
        )
        api = tmp_path / "api.ts"
        worker = tmp_path / "worker.ts"
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("api.Service.handle", NodeType.METHOD, str(api), line_start=2, line_end=2, col_start=4))
        graph.add_node(
            Node("worker.Worker.handle", NodeType.METHOD, str(worker), line_start=5, line_end=7, col_start=4)
        )

        client = MagicMock()
        client.send_definition_batch.side_effect = lambda queries: [
            [{"uri": api.as_uri(), "range": {"start": {"line": 1, "character": 4}}}] for _ in queries
        ]
        client.send_implementation_batch.side_effect = lambda queries: [
            [{"uri": worker.as_uri(), "range": {"start": {"line": 4, "character": 4}}}] for _ in queries
        ]

        _add_outbound_edges_from_changed_files(
            GraphIndex(graph, SourceInspector()), [changed], client, SourceInspector(), self._typescript_adapter()
        )

        asked = [query for call in client.send_implementation_batch.call_args_list for query in call.args[0]]
        assert asked == [(api, 1, 4)]
        edges = [(edge.get_source(), edge.get_destination()) for edge in graph.edges]
        assert ("app.run", "worker.Worker.handle") in edges

    def test_a_collection_initializer_that_constructs_keeps_its_constructor_edge(self, tmp_path: Path) -> None:
        """``new Bag { 1 }`` is one site with two shapes, and a full build gives it both.

        The engine decides constructor expansion per site, from the source; deciding it from
        the call-site kind instead leaves a warm start with the ``Add`` edge but no constructor.
        """
        changed = tmp_path / "app.cs"
        changed.write_text("class App\n{\n    void Run()\n    {\n        var bag = new Bag { 1 };\n    }\n}\n")
        bag = tmp_path / "bag.cs"
        graph = CallGraph(language="csharp")
        graph.add_node(Node("app.App.Run", NodeType.METHOD, str(changed), line_start=3, line_end=6, col_start=9))
        graph.add_node(Node("bag.Bag", NodeType.CLASS, str(bag), line_start=1, line_end=4, col_start=6))
        graph.add_node(Node("bag.Bag.Bag", NodeType.CONSTRUCTOR, str(bag), line_start=2, line_end=2, col_start=11))
        graph.add_node(Node("bag.Bag.Add", NodeType.METHOD, str(bag), line_start=3, line_end=3, col_start=9))

        adapter = MagicMock()
        adapter.language_id = "csharp"
        adapter.resolves_method_groups = False
        adapter.resolves_collection_initializers = True
        adapter.resolves_iterated_types = False
        adapter.expands_virtual_dispatch = False
        adapter.expands_constructors = True

        client = MagicMock()
        client.send_definition_batch.side_effect = lambda queries: [
            [{"uri": bag.as_uri(), "range": {"start": {"line": 0, "character": 6}}}] for _ in queries
        ]
        client.send_implementation_batch.side_effect = lambda queries: [[] for _ in queries]

        _add_outbound_edges_from_changed_files(
            GraphIndex(graph, SourceInspector()), [changed], client, SourceInspector(), adapter
        )

        edges = [(edge.get_source(), edge.get_destination()) for edge in graph.edges]
        assert ("app.App.Run", "bag.Bag.Add") in edges
        assert ("app.App.Run", "bag.Bag.Bag") in edges

    def test_a_loop_over_a_construction_keeps_its_constructor_edge(self, tmp_path: Path) -> None:
        """A loop subject can be a construction too, and a full build expands it as one."""
        changed = tmp_path / "app.cs"
        changed.write_text(
            "class App\n{\n    void Run()\n    {\n        foreach (var x in new Bag { 1 }) { }\n    }\n}\n"
        )
        bag = tmp_path / "bag.cs"
        graph = CallGraph(language="csharp")
        graph.add_node(Node("app.App.Run", NodeType.METHOD, str(changed), line_start=3, line_end=6, col_start=9))
        graph.add_node(Node("bag.Bag", NodeType.CLASS, str(bag), line_start=1, line_end=4, col_start=6))
        graph.add_node(Node("bag.Bag.Bag", NodeType.CONSTRUCTOR, str(bag), line_start=2, line_end=2, col_start=11))

        adapter = MagicMock()
        adapter.language_id = "csharp"
        adapter.resolves_method_groups = False
        adapter.resolves_collection_initializers = False
        adapter.resolves_iterated_types = True
        adapter.expands_virtual_dispatch = False
        adapter.expands_constructors = True

        client = MagicMock()
        client.send_type_definition_batch.side_effect = lambda queries: [
            [{"uri": bag.as_uri(), "range": {"start": {"line": 0, "character": 6}}}] for _ in queries
        ]
        client.send_definition_batch.side_effect = lambda queries: [[] for _ in queries]
        client.send_implementation_batch.side_effect = lambda queries: [[] for _ in queries]

        _add_outbound_edges_from_changed_files(
            GraphIndex(graph, SourceInspector()), [changed], client, SourceInspector(), adapter
        )

        edges = [(edge.get_source(), edge.get_destination()) for edge in graph.edges]
        assert ("app.App.Run", "bag.Bag.Bag") in edges

    def test_an_implementation_in_a_changed_file_still_gets_its_edge(self, tmp_path: Path) -> None:
        """The caller and the implementation change together; their interface does not.

        The partial build has no symbol for the unchanged interface, so it cannot make the
        edge at all; treating the implementation's file as already handled loses it for good.
        """
        changed = tmp_path / "app.ts"
        changed.write_text(
            'import { Service } from "./api";\n\nexport function run(s: Service) {\n    s.handle();\n}\n'
        )
        worker = tmp_path / "worker.ts"
        api = tmp_path / "api.ts"
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("api.Service.handle", NodeType.METHOD, str(api), line_start=2, line_end=2, col_start=4))
        graph.add_node(
            Node("worker.Worker.handle", NodeType.METHOD, str(worker), line_start=5, line_end=7, col_start=4)
        )

        client = MagicMock()
        client.send_definition_batch.side_effect = lambda queries: [
            [{"uri": api.as_uri(), "range": {"start": {"line": 1, "character": 4}}}] for _ in queries
        ]
        client.send_implementation_batch.side_effect = lambda queries: [
            [{"uri": worker.as_uri(), "range": {"start": {"line": 4, "character": 4}}}] for _ in queries
        ]

        # Both the caller and the implementation are in this edit.
        _add_outbound_edges_from_changed_files(
            GraphIndex(graph, SourceInspector()),
            [changed, worker],
            client,
            SourceInspector(),
            self._typescript_adapter(),
        )

        edges = [(edge.get_source(), edge.get_destination()) for edge in graph.edges]
        assert ("app.run", "worker.Worker.handle") in edges

    def test_a_callback_bound_to_a_const_is_a_method_group_target(self, tmp_path: Path) -> None:
        helpers = tmp_path / "helpers.ts"
        helpers.write_text("export const handler = () => 1;\n")
        changed = tmp_path / "app.ts"
        changed.write_text(
            "import { handler } from './helpers';\n\nexport function run() {\n    subscribe(handler);\n}\n"
        )
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("helpers.handler", NodeType.VARIABLE, str(helpers), line_start=1, line_end=1, col_start=13))

        assert self._run(graph, changed, {14: (str(helpers), 0, 13)}) == [("app.run", "helpers.handler")]

    def test_a_constant_passed_as_an_argument_is_not_an_edge(self, tmp_path: Path) -> None:
        helpers = tmp_path / "helpers.ts"
        helpers.write_text("export const LIMIT = 5;\n")
        changed = tmp_path / "app.ts"
        changed.write_text("import { LIMIT } from './helpers';\n\nexport function run() {\n    subscribe(LIMIT);\n}\n")
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("helpers.LIMIT", NodeType.VARIABLE, str(helpers), line_start=1, line_end=1, col_start=13))

        assert self._run(graph, changed, {14: (str(helpers), 0, 13)}) == []

    def test_a_member_call_that_leaves_the_repository_resolves_through_its_receiver(self, tmp_path: Path) -> None:
        logger_file = tmp_path / "log.ts"
        logger_file.write_text("export const log = { warn(m: string) {} };\n")
        changed = tmp_path / "app.ts"
        changed.write_text("import { log } from './log';\n\nexport function run() {\n    log.warn('x');\n}\n")
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("log.log", NodeType.VARIABLE, str(logger_file), line_start=1, line_end=1, col_start=13))
        graph.add_node(Node("log.log.warn", NodeType.METHOD, str(logger_file), line_start=1, line_end=1, col_start=21))

        assert self._run(graph, changed, {4: (str(logger_file), 0, 13)}) == [("app.run", "log.log.warn")]
