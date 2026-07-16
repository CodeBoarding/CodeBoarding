"""Tests for the in-memory CFG-update helpers used by the warm-start flow.

The warm-start flow loads a prior pkl, asks git for files changed since the
pkl's tag SHA, and uses ``invalidate_files`` + ``merge_results`` to bring
the cached analysis-dict up to date in memory before saving a new pkl.
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.analysis_cache import invalidate_files, merge_results
from static_analyzer.analysis_result import AnalysisData, StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node
from static_analyzer.incremental_orchestrator import (
    _definition_nodes,
    _restore_cross_boundary_edges,
    update_cfg_for_changed_files,
)
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
            [(src, dst) for src, dst, _src_node, _dst_node in updated.invalidated_edges],
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
                engine_client,
                ignore_manager,
            )

            self.assertNotIn("a.foo", updated["call_graph"].nodes)
            self.assertIn("b.bar", updated["call_graph"].nodes)
            self.assertEqual([str(path) for path in updated["source_files"]], [str(live_file)])


class TestWarmStartOutboundEdges(unittest.TestCase):
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

        matches = _definition_nodes(call_graph, definition)

        self.assertEqual(
            [node.fully_qualified_name for node in matches],
            [
                "pkg.converter.DocumentConverter.convert",
                "pkg.converter.DocumentConverter",
            ],
        )

    def test_cached_outbound_edge_is_restored_from_live_non_call_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            changed_file = Path(temp_dir) / "changed.py"
            target_file = Path(temp_dir) / "target.py"
            changed_file.write_text("def convert():\n    return result.text_content\n", encoding="utf-8")
            target_file.write_text("class Result:\n    def text_content(self): ...\n", encoding="utf-8")
            source = Node(
                fully_qualified_name="changed.convert",
                node_type=NodeType.FUNCTION,
                file_path=str(changed_file),
                line_start=1,
                line_end=2,
            )
            target = Node(
                fully_qualified_name="target.Result.text_content",
                node_type=NodeType.METHOD,
                file_path=str(target_file),
                line_start=2,
                line_end=2,
                col_start=4,
            )
            call_graph = CallGraph(language="python")
            call_graph.add_node(source)
            call_graph.add_node(target)
            engine_client = MagicMock()
            engine_client.references.return_value = [
                {
                    "uri": changed_file.as_uri(),
                    "range": {
                        "start": {"line": 1, "character": 11},
                        "end": {"line": 1, "character": 30},
                    },
                }
            ]
            adapter = MagicMock()
            adapter.language_id = "python"
            adapter.is_class_like.return_value = False

            _restore_cross_boundary_edges(
                call_graph,
                [(source.fully_qualified_name, target.fully_qualified_name, source, target)],
                {str(changed_file)},
                adapter,
                engine_client,
                SourceInspector(),
            )

            self.assertEqual(len(call_graph.edges), 1)
            self.assertEqual(
                call_graph.edges[0].call_sites,
                [{"file": str(changed_file), "line": 2, "column": 12}],
            )


if __name__ == "__main__":
    unittest.main()
