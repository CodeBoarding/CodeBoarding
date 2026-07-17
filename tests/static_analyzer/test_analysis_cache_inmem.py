"""Tests for the in-memory graph-update helpers used by the warm-start flow.

The warm-start flow loads a prior pkl, asks for the files changed since the
pkl's tag SHA, and uses ``ProgramGraph.without_files`` + ``merge`` to bring the
cached graph up to date in memory before saving a new pkl.
"""

import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult, InfomapClusterSnapshot
from static_analyzer.constants import Language, NodeType
from static_analyzer.program_graph import ProgramEdge, ProgramEdgeKind, ProgramGraph, ProgramOccurrence
from tests.program_graph_factory import make_symbol
from utils import CODEBOARDING_DIR_NAME


def _graph(specs: list[tuple[str, str, int]]) -> ProgramGraph:
    graph = ProgramGraph(language="python")
    for qname, file_path, line_start in specs:
        graph.add_node(make_symbol(qname, NodeType.FUNCTION, file_path, line_start, line_start + 1, language="python"))
    return graph


def _call(graph: ProgramGraph, source: str, target: str) -> None:
    graph.add_edge(
        ProgramEdge(ProgramEdgeKind.CALL, source, target, [ProgramOccurrence(graph.nodes[source].file_path, 1, 1)])
    )


def _graph_with_lineage() -> ProgramGraph:
    graph = _graph([("a.foo", "a.py", 1), ("a.bar", "a.py", 10), ("b.qux", "b.py", 1)])
    graph.cluster_snapshot = InfomapClusterSnapshot(
        cluster_result=ClusterResult(
            clusters={1: {"a.foo", "a.bar"}, 2: {"b.qux"}},
            cluster_to_files={1: {"a.py"}, 2: {"b.py"}},
            file_to_clusters={"a.py": {1}, "b.py": {2}},
            strategy="hierarchical_infomap",
        ),
        node_paths={"a.foo": (1,), "a.bar": (1,), "b.qux": (2,)},
        module_members={1: {"a.foo", "a.bar"}, 2: {"b.qux"}},
        next_cluster_id=3,
    )
    return graph


class TestWithoutFiles(unittest.TestCase):
    def test_drops_symbols_from_changed_files(self) -> None:
        updated = _graph_with_lineage().without_files({"a.py"})

        self.assertEqual(set(updated.symbols), {"b.qux"})

    def test_drops_edges_whose_endpoint_was_dropped(self) -> None:
        graph = _graph([("a.foo", "a.py", 1), ("b.qux", "b.py", 1)])
        _call(graph, "a.foo", "b.qux")

        updated = graph.without_files({"a.py"})

        self.assertEqual(updated.call_edges(), [])
        self.assertNotIn("a.foo", updated.symbols)
        self.assertIn("b.qux", updated.symbols)

    def test_returns_an_independent_graph(self) -> None:
        graph = _graph_with_lineage()

        graph.without_files({"a.py"})

        self.assertEqual(set(graph.symbols), {"a.foo", "a.bar", "b.qux"})
        assert graph.cluster_snapshot is not None
        self.assertEqual(graph.cluster_snapshot.module_members, {1: {"a.foo", "a.bar"}, 2: {"b.qux"}})


class TestClusterLineagePreservation(unittest.TestCase):
    """The lineage must survive warm-start invalidation/merge.

    Regression: dropping it caused ``IncrementalCacheMissingError`` even when
    the pkl on disk had a populated snapshot.
    """

    def test_without_files_keeps_lineage_for_surviving_symbols(self) -> None:
        updated = _graph_with_lineage().without_files({"a.py"})

        snapshot = updated.cluster_snapshot
        assert snapshot is not None
        # Cluster 1 had only a.py members -> pruned out. Cluster 2 keeps b.qux.
        self.assertEqual(snapshot.module_members, {2: {"b.qux"}})
        self.assertEqual(snapshot.node_paths, {"b.qux": (2,)})

    def test_without_files_keeps_partially_surviving_cluster(self) -> None:
        # Invalidate only b.py: cluster 1 (members in a.py) survives whole,
        # cluster 2 (b.qux only) is pruned out.
        updated = _graph_with_lineage().without_files({"b.py"})

        snapshot = updated.cluster_snapshot
        assert snapshot is not None
        self.assertEqual(snapshot.module_members, {1: {"a.foo", "a.bar"}})

    def test_merge_preserves_cached_lineage(self) -> None:
        base = _graph_with_lineage().without_files({"a.py"})
        delta = _graph([("a.foo", "a.py", 1)])

        base.merge(delta)

        snapshot = base.cluster_snapshot
        assert snapshot is not None
        self.assertEqual(set(base.symbols), {"a.foo", "b.qux"})
        # The re-analyzed symbol is intentionally unseeded: the clusterer starts
        # it as a singleton and reconciles ids by overlap.
        self.assertEqual(snapshot.module_members, {2: {"b.qux"}})

    def test_merge_unions_disjoint_graphs(self) -> None:
        base = _graph([("a.foo", "a.py", 1)])
        delta = _graph([("c.new", "c.py", 1)])

        base.merge(delta)

        self.assertEqual(set(base.symbols), {"a.foo", "c.new"})


class TestCachePersistence(unittest.TestCase):
    def test_does_not_persist_incremental_base_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = StaticAnalysisResults()
            result.add_program_graph(Language.PYTHON, _graph_with_lineage())
            base_results = StaticAnalysisResults()
            result.incremental_base_results = base_results

            cache = StaticAnalysisCache(root / CODEBOARDING_DIR_NAME, root)
            cache.save(result, source_sha="sha")
            loaded = cache.get()

            assert loaded is not None
            self.assertIsNone(loaded.incremental_base_results)
            self.assertIs(result.incremental_base_results, base_results)

    def test_round_trips_cluster_lineage_between_repo_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            repo_a = temp_path / "repo-a"
            repo_b = temp_path / "repo-b"
            artifact_dir = temp_path / "artifact"
            repo_a.mkdir()
            repo_b.mkdir()

            source_file = repo_a / "pkg" / "a.py"
            graph = _graph([("pkg.a.foo", str(source_file), 1)])
            graph.cluster_snapshot = InfomapClusterSnapshot(
                cluster_result=ClusterResult(
                    clusters={1: {"pkg.a.foo"}},
                    cluster_to_files={1: {str(source_file)}},
                    file_to_clusters={str(source_file): {1}},
                    strategy="hierarchical_infomap",
                ),
                node_paths={"pkg.a.foo": (1,)},
                module_members={1: {"pkg.a.foo"}},
            )
            result = StaticAnalysisResults()
            result.add_program_graph(Language.PYTHON, graph)

            StaticAnalysisCache(artifact_dir, repo_a).save(result, source_sha="sha")
            loaded = StaticAnalysisCache(artifact_dir, repo_b).get(expected_sha="sha")

            assert loaded is not None
            loaded_graph = loaded.get_program_graph(Language.PYTHON)
            expected_file = str(repo_b.resolve() / "pkg" / "a.py")
            self.assertEqual(loaded_graph.symbols["pkg.a.foo"].file_path, expected_file)
            assert loaded_graph.cluster_snapshot is not None
            self.assertEqual(loaded_graph.cluster_snapshot.cluster_result.cluster_to_files, {1: {expected_file}})


if __name__ == "__main__":
    unittest.main()
