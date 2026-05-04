"""Tests for the in-memory CFG-update helpers used by the warm-start flow.

The warm-start flow loads a prior pkl, asks git for files changed since the
pkl's tag SHA, and uses ``invalidate_files`` + ``merge_results`` to bring
the cached analysis-dict up to date in memory before saving a new pkl.
"""

import unittest
from pathlib import Path

from static_analyzer.analysis_cache import invalidate_files, merge_results
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


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


class TestInvalidateFiles(unittest.TestCase):
    def test_drops_nodes_from_changed_files(self) -> None:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cached = _result(cg, source_files=["a.py", "b.py"])

        updated = invalidate_files(cached, {Path("a.py")})

        self.assertNotIn("a.foo", updated["call_graph"].nodes)
        self.assertIn("b.bar", updated["call_graph"].nodes)
        self.assertEqual([str(p) for p in updated["source_files"]], ["b.py"])

    def test_cascades_edges_when_endpoint_dropped(self) -> None:
        # Edge a.foo -> b.bar must be dropped when a.foo is removed; the
        # remaining b.bar must stay and the dangling-edge guard must not fire.
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cg.add_edge("a.foo", "b.bar")
        cached = _result(cg, source_files=["a.py", "b.py"])

        updated = invalidate_files(cached, {Path("a.py")})

        self.assertEqual(len(updated["call_graph"].edges), 0)

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

        updated = invalidate_files(cached, {Path("a.py")})

        self.assertEqual([r.fully_qualified_name for r in updated["references"]], ["b.bar"])
        self.assertEqual(set(updated["class_hierarchies"].keys()), {"B"})
        self.assertEqual(updated["package_relations"]["pkg"]["files"], ["b.py"])

    def test_diagnostics_preserved_for_unchanged_files(self) -> None:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cached = _result(cg, source_files=["a.py", "b.py"])
        cached["diagnostics"] = {"a.py": ["d1"], "b.py": ["d2"]}

        updated = invalidate_files(cached, {Path("a.py")})

        self.assertEqual(updated["diagnostics"], {"b.py": ["d2"]})


class TestMergeResults(unittest.TestCase):
    def test_unions_disjoint_call_graphs(self) -> None:
        cached_cg = CallGraph(language="python")
        cached_cg.add_node(_node("a.foo", "a.py"))
        new_cg = CallGraph(language="python")
        new_cg.add_node(_node("b.bar", "b.py"))

        merged = merge_results(_result(cached_cg, source_files=["a.py"]), _result(new_cg, source_files=["b.py"]))

        self.assertEqual(set(merged["call_graph"].nodes), {"a.foo", "b.bar"})

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

        merged = merge_results(cached, new)

        self.assertEqual([(r.fully_qualified_name, r.line_start) for r in merged["references"]], [("b.bar", 20)])

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

        merged = merge_results(cached, new)

        names = sorted((r.fully_qualified_name, r.line_start) for r in merged["references"])
        self.assertEqual(names, [("a.foo", 1), ("b.bar", 99)])

    def test_diagnostics_merge_with_new_winning(self) -> None:
        cached = _result(CallGraph(language="python"), source_files=["a.py", "b.py"])
        cached["diagnostics"] = {"a.py": ["old-a"], "b.py": ["old-b"]}
        new = _result(CallGraph(language="python"), source_files=["b.py"])
        new["diagnostics"] = {"b.py": ["new-b"]}

        merged = merge_results(cached, new)

        self.assertEqual(merged["diagnostics"], {"a.py": ["old-a"], "b.py": ["new-b"]})


if __name__ == "__main__":
    unittest.main()
