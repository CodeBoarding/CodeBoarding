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
from static_analyzer.graph_definitions import GraphIndex, call_shapes, implemented_by
from static_analyzer.incremental_orchestrator import (
    _restore_inbound_edges_via_definitions,
    update_cfg_for_changed_files,
)
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.models import CallSite, ExternalCallSite
from static_analyzer.engine.utils import definition_location
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


def _declared(index: GraphIndex, definition: dict) -> Node | None:
    location = definition_location(definition)
    return index.declaration_at(str(location[0]), location[1], location[2]) if location else None


class TestGraphDeclarations(unittest.TestCase):
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

        declared = _declared(index, link)
        self.assertEqual(declared.fully_qualified_name if declared else None, "unchanged.unchanged_target")
        self.assertIsNone(_declared(index, whole_declaration))

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

        self.assertIsNone(_declared(GraphIndex(call_graph, SourceInspector()), definition))

    def test_an_implementing_constructor_reaches_its_class(self) -> None:
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
        client = MagicMock()
        client.send_implementation_batch.side_effect = lambda queries: [
            [{"uri": file_path.as_uri(), "range": {"start": {"line": 1, "character": 5}}}] for _ in queries
        ]
        declaration = ("/repo/pkg/base.php", 0, 0)

        found = implemented_by(GraphIndex(call_graph, SourceInspector()), client, [declaration])

        self.assertEqual(
            [node.fully_qualified_name for node in found[declaration]],
            ["pkg.target.Target.__construct", "pkg.target.Target"],
        )

    def test_an_implementing_method_does_not_reach_its_class(self) -> None:
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
        client = MagicMock()
        client.send_implementation_batch.side_effect = lambda queries: [
            [{"uri": file_path.as_uri(), "range": {"start": {"line": 9, "character": 4}}}] for _ in queries
        ]
        declaration = ("/repo/pkg/base.py", 0, 0)

        found = implemented_by(GraphIndex(call_graph, SourceInspector()), client, [declaration])

        self.assertEqual(
            [node.fully_qualified_name for node in found[declaration]], ["pkg.converter.DocumentConverter.convert"]
        )


if __name__ == "__main__":
    unittest.main()


class TestWarmStartLinksWhatThePartialBuildCouldNotName:
    def test_calls_into_unchanged_files_are_linked_and_the_rest_handed_back(self, tmp_path: Path) -> None:
        """The partial build holds only the changed files; the merged graph names the rest, and
        what it cannot name either may belong to another solution's engine."""
        lib = tmp_path / "lib.py"
        lib.write_text("def helper():\n    return 1\n")
        app = tmp_path / "app.py"
        app.write_text("def main():\n    helper()\n")
        cached_graph = CallGraph(language="python")
        cached_graph.add_node(Node("lib.helper", NodeType.FUNCTION, str(lib), line_start=1, line_end=2, col_start=4))
        cached = _result(cached_graph, source_files=[str(lib), str(app)])

        partial_graph = CallGraph(language="python")
        partial_graph.add_node(Node("app.main", NodeType.FUNCTION, str(app), line_start=1, line_end=2, col_start=4))
        call = CallSite.from_lsp_position(str(app), 1, 4)
        linkable = ExternalCallSite("app.main", str(lib), 0, 4, call)
        elsewhere = ExternalCallSite("app.main", str(tmp_path / "vendor.py"), 3, 0, call)
        partial = {
            **_result(partial_graph, source_files=[str(app)]),
            "diagnostics": {},
            "external_call_sites": [linkable, elsewhere],
        }

        client = MagicMock()
        client.get_collected_diagnostics.return_value = {}
        client.send_implementation_batch.side_effect = lambda queries: [[] for _ in queries]
        ignore_manager = MagicMock()
        ignore_manager.should_ignore.return_value = False

        with (
            patch("static_analyzer.incremental_orchestrator.CallGraphBuilder"),
            patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format", return_value=partial),
        ):
            updated = update_cfg_for_changed_files(
                cached, {app}, PythonAdapter(), tmp_path, tmp_path, client, ignore_manager
            )

        edges = [(edge.get_source(), edge.get_destination()) for edge in updated["call_graph"].edges]
        assert edges == [("app.main", "lib.helper")]
        assert updated["external_call_sites"] == [elsewhere]


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
        shapes = call_shapes(source, SourceInspector(), self._adapter(), set())

        assert shapes.requests_at((4, 26)) == [("definition", "call"), ("type_definition", "iterated")]

    def test_a_loop_over_a_variable_is_asked_only_for_its_type(self, tmp_path: Path) -> None:
        source = tmp_path / "Loop.cs"
        source.write_text("class A\n{\n    void Run(int[] bag)\n    {\n        foreach (var x in bag) { }\n    }\n}\n")
        shapes = call_shapes(source, SourceInspector(), self._adapter(), set())

        assert shapes.requests_at((4, 26)) == [("type_definition", "iterated")]
