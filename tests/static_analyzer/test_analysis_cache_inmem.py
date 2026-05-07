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
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node
from static_analyzer.incremental_orchestrator import update_cfg_for_changed_files


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


class TestClusterCachePreservation(unittest.TestCase):
    """``_cluster_cache`` must survive warm-start invalidation/merge.

    Regression: dropping it caused ``IncrementalCacheMissingError`` even
    when the pkl on disk had a populated cache.
    """

    def _cg_with_cluster_cache(self) -> CallGraph:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py", line_start=1))
        cg.add_node(_node("a.bar", "a.py", line_start=10))
        cg.add_node(_node("b.qux", "b.py", line_start=1))
        cg._cluster_cache = ClusterResult(
            clusters={1: {"a.foo", "a.bar"}, 2: {"b.qux"}},
            cluster_to_files={1: {"a.py"}, 2: {"b.py"}},
            file_to_clusters={"a.py": {1}, "b.py": {2}},
            strategy="leiden",
        )
        return cg

    def test_invalidate_files_preserves_cluster_cache_for_kept_files(self) -> None:
        cached = _result(self._cg_with_cluster_cache(), source_files=["a.py", "b.py"])

        updated = invalidate_files(cached, {Path("a.py")})

        cc = updated["call_graph"]._cluster_cache
        self.assertIsNotNone(cc)
        # Cluster 1 had only a.py members -> dropped entirely.
        # Cluster 2 keeps b.qux from b.py.
        self.assertNotIn(1, cc.clusters)
        self.assertEqual(cc.clusters[2], {"b.qux"})
        self.assertEqual(cc.cluster_to_files[2], {"b.py"})
        self.assertEqual(cc.file_to_clusters, {"b.py": {2}})
        self.assertEqual(cc.strategy, "leiden")

    def test_invalidate_files_preserves_partial_cluster(self) -> None:
        cached = _result(self._cg_with_cluster_cache(), source_files=["a.py", "b.py"])
        # Only invalidate b.py; cluster 1 (members in a.py) survives whole;
        # cluster 2 (b.qux only) drops.
        updated = invalidate_files(cached, {Path("b.py")})

        cc = updated["call_graph"]._cluster_cache
        self.assertEqual(cc.clusters[1], {"a.foo", "a.bar"})
        self.assertNotIn(2, cc.clusters)

    def test_merge_results_preserves_cached_cluster_cache(self) -> None:
        cached = _result(self._cg_with_cluster_cache(), source_files=["a.py", "b.py"])
        new_cg = CallGraph(language="python")
        new_cg.add_node(_node("c.new", "c.py"))
        new = _result(new_cg, source_files=["c.py"])

        merged = merge_results(cached, new)

        cc = merged["call_graph"]._cluster_cache
        # Cached clusters survive; new node 'c.new' is unclustered (intentional —
        # cluster_delta will pick it up as drift on the next run).
        self.assertEqual(cc.clusters[1], {"a.foo", "a.bar"})
        self.assertEqual(cc.clusters[2], {"b.qux"})

    def test_filter_returns_independent_call_graph(self) -> None:
        # Ensure CallGraph.filter does not mutate the source.
        cg = self._cg_with_cluster_cache()
        original_node_count = len(cg.nodes)
        assert cg._cluster_cache is not None
        original_cluster_ids = set(cg._cluster_cache.clusters.keys())

        cg.filter(lambda n: n.file_path != "a.py")

        self.assertEqual(len(cg.nodes), original_node_count)
        assert cg._cluster_cache is not None
        self.assertEqual(set(cg._cluster_cache.clusters.keys()), original_cluster_ids)

    def test_filter_drops_edges_with_dropped_endpoint(self) -> None:
        cg = CallGraph(language="python")
        cg.add_node(_node("a.foo", "a.py"))
        cg.add_node(_node("b.bar", "b.py"))
        cg.add_edge("a.foo", "b.bar")

        filtered = cg.filter(lambda n: n.file_path != "a.py")

        self.assertEqual(len(filtered.edges), 0)
        self.assertNotIn("a.foo", filtered.nodes)
        self.assertIn("b.bar", filtered.nodes)

    def test_union_preserves_cached_side_cluster_cache(self) -> None:
        cached = self._cg_with_cluster_cache()
        new = CallGraph(language="python")
        new.add_node(_node("c.new", "c.py"))

        unioned = cached.union(new)

        cc = unioned._cluster_cache
        assert cc is not None
        self.assertEqual(cc.clusters, {1: {"a.foo", "a.bar"}, 2: {"b.qux"}})
        # New node from `other` participates in the graph but not yet in any cluster.
        self.assertIn("c.new", unioned.nodes)
        self.assertNotIn("c.new", {m for members in cc.clusters.values() for m in members})


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


if __name__ == "__main__":
    unittest.main()
