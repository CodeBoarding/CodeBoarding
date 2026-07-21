import unittest
from unittest.mock import MagicMock, patch

import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import (
    build_all_cluster_results,
    enforce_cross_language_budget,
    reindex_cluster_result,
    subgraph_peak_modularity,
    supercluster_by_modularity_peak,
    supercluster_leaf_ids,
    MAX_LLM_CLUSTERS,
    TOP_LEVEL_COMPONENTS_MAX,
    TOP_LEVEL_COMPONENTS_MIN,
)
from static_analyzer.graph import ClusterResult


class TestClusterHelpers(unittest.TestCase):
    @staticmethod
    def _make_cluster_result(prefix: str, count: int) -> ClusterResult:
        clusters = {cluster_id: {f"{prefix}.node_{cluster_id}"} for cluster_id in range(1, count + 1)}
        cluster_to_files = {cluster_id: {f"/repo/{prefix}_{cluster_id}.py"} for cluster_id in range(1, count + 1)}
        file_to_clusters = {f"/repo/{prefix}_{cluster_id}.py": {cluster_id} for cluster_id in range(1, count + 1)}
        return ClusterResult(
            clusters=clusters,
            cluster_to_files=cluster_to_files,
            file_to_clusters=file_to_clusters,
            strategy="test",
        )

    def test_multi_tech_stack_cluster_ids_are_reindexed_without_overlap(self):
        analysis = MagicMock(spec=StaticAnalysisResults)
        analysis.get_languages.return_value = ["python", "typescript"]

        python_cfg = MagicMock()
        typescript_cfg = MagicMock()

        python_cfg.cluster.return_value = self._make_cluster_result("py", 40)
        typescript_cfg.cluster.return_value = self._make_cluster_result("ts", 40)
        python_cfg.to_networkx.return_value = object()
        typescript_cfg.to_networkx.return_value = object()

        analysis.get_cfg.side_effect = lambda language: {
            "python": python_cfg,
            "typescript": typescript_cfg,
        }[language]

        def _fake_merge(cluster_result: ClusterResult, _cfg_graph: object, target: int) -> ClusterResult:
            first_file = next(iter(cluster_result.file_to_clusters))
            prefix = "py" if first_file.startswith("/repo/py_") else "ts"
            return self._make_cluster_result(prefix, target)

        with patch("static_analyzer.cluster_helpers.merge_clusters", side_effect=_fake_merge) as mock_merge:
            result = build_all_cluster_results(analysis)

        self.assertEqual(mock_merge.call_count, 2)
        self.assertEqual([call.args[2] for call in mock_merge.call_args_list], [25, 25])

        python_ids = set(result["python"].clusters.keys())
        typescript_ids = set(result["typescript"].clusters.keys())
        self.assertEqual(python_ids, set(range(1, 26)))
        self.assertEqual(typescript_ids, set(range(26, 51)))
        self.assertTrue(python_ids.isdisjoint(typescript_ids))

        shifted_ts_ids = set().union(*result["typescript"].file_to_clusters.values())
        self.assertEqual(shifted_ts_ids, set(range(26, 51)))
        self.assertIs(python_cfg._cluster_cache, result["python"])
        self.assertIs(typescript_cfg._cluster_cache, result["typescript"])

    def test_reindex_cluster_result_shifts_all_ids(self):
        cr = self._make_cluster_result("x", 3)
        shifted = reindex_cluster_result(cr, 10)

        self.assertEqual(set(shifted.clusters.keys()), {11, 12, 13})
        self.assertEqual(set(shifted.cluster_to_files.keys()), {11, 12, 13})
        for file_ids in shifted.file_to_clusters.values():
            self.assertTrue(file_ids.issubset({11, 12, 13}))

    def test_enforce_cross_language_budget_reindexes_without_overlap(self):
        """IDs must be unique across languages even when total <= MAX_LLM_CLUSTERS."""
        cluster_results = {
            "javascript": self._make_cluster_result("js", 10),
            "python": self._make_cluster_result("py", 10),
        }
        cfg_graphs = {
            "javascript": nx.DiGraph(),
            "python": nx.DiGraph(),
        }

        enforce_cross_language_budget(cluster_results, cfg_graphs)

        js_ids = set(cluster_results["javascript"].clusters.keys())
        py_ids = set(cluster_results["python"].clusters.keys())
        self.assertTrue(js_ids.isdisjoint(py_ids), f"Overlap detected: {js_ids & py_ids}")
        self.assertEqual(len(js_ids) + len(py_ids), 20)

    def test_enforce_cross_language_budget_reduces_when_over_limit(self):
        """Combined clusters exceeding MAX_LLM_CLUSTERS must be proportionally reduced."""
        cluster_results = {
            "javascript": self._make_cluster_result("js", 30),
            "python": self._make_cluster_result("py", 40),
        }
        cfg_graphs = {
            "javascript": nx.DiGraph(),
            "python": nx.DiGraph(),
        }

        with patch("static_analyzer.cluster_helpers.merge_clusters") as mock_merge:
            mock_merge.side_effect = lambda cr, _g, target: self._make_cluster_result(
                "js" if next(iter(cr.file_to_clusters)).startswith("/repo/js_") else "py",
                target,
            )
            enforce_cross_language_budget(cluster_results, cfg_graphs)

        total = sum(len(cr.clusters) for cr in cluster_results.values())
        self.assertLessEqual(total, MAX_LLM_CLUSTERS)

        js_ids = set(cluster_results["javascript"].clusters.keys())
        py_ids = set(cluster_results["python"].clusters.keys())
        self.assertTrue(js_ids.isdisjoint(py_ids), f"Overlap detected: {js_ids & py_ids}")

    def test_enforce_cross_language_budget_noop_for_single_language(self):
        """Single-language results should not be modified."""
        cr = self._make_cluster_result("py", 10)
        cluster_results = {"python": cr}
        cfg_graphs = {"python": nx.DiGraph()}

        enforce_cross_language_budget(cluster_results, cfg_graphs)

        self.assertIs(cluster_results["python"], cr)
        self.assertEqual(set(cr.clusters.keys()), set(range(1, 11)))


class TestSuperClusterModularityPeak(unittest.TestCase):
    @staticmethod
    def _blocks(n_blocks: int, per_block: int, weak_bridges: bool = True):
        """A ClusterResult + meta-friendly cfg with ``n_blocks`` tight blocks of leaf clusters."""
        clusters, cluster_to_files, file_to_clusters = {}, {}, {}
        graph = nx.DiGraph()
        n = n_blocks * per_block
        for cid in range(1, n + 1):
            block = (cid - 1) // per_block
            nodes = [f"n{cid}_{j}" for j in range(3)]
            clusters[cid] = set(nodes)
            path = f"/repo/block{block}/c{cid}.py"
            cluster_to_files[cid] = {path}
            file_to_clusters[path] = {cid}
            for node in nodes:
                graph.add_node(node, file_path=path)
        # Dense calls within a block, so each block is a tight community.
        for cid in range(1, n + 1):
            block = (cid - 1) // per_block
            for other in range(1, n + 1):
                if other != cid and (other - 1) // per_block == block:
                    graph.add_edge(f"n{cid}_0", f"n{other}_1")
        if weak_bridges:
            for block in range(n_blocks - 1):
                graph.add_edge(f"n{block * per_block + 1}_0", f"n{(block + 1) * per_block + 1}_1")
        cr = ClusterResult(
            clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
        )
        return cr, graph

    def _assert_partition(self, groups, expected_ids):
        assigned = [cid for group in groups for cid in group]
        self.assertEqual(sorted(assigned), sorted(expected_ids))
        self.assertEqual(len(assigned), len(set(assigned)), "clusters must be partitioned, not shared")

    def test_recovers_natural_block_count_within_range(self):
        cr, graph = self._blocks(n_blocks=6, per_block=5)
        groups = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(len(groups), 6)
        self._assert_partition(groups, range(1, 31))

    def test_count_is_clamped_to_range(self):
        # 10 natural blocks, but the count must not exceed the max.
        cr, graph = self._blocks(n_blocks=10, per_block=4)
        groups = supercluster_by_modularity_peak(cr, graph)
        self.assertTrue(TOP_LEVEL_COMPONENTS_MIN <= len(groups) <= TOP_LEVEL_COMPONENTS_MAX)
        self._assert_partition(groups, range(1, 41))

    def test_deterministic_across_runs(self):
        cr, graph = self._blocks(n_blocks=7, per_block=4)
        first = supercluster_by_modularity_peak(cr, graph)
        second = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(sorted(map(sorted, first)), sorted(map(sorted, second)))

    def test_fewer_clusters_than_floor_returns_singletons(self):
        cr, graph = self._blocks(n_blocks=1, per_block=3, weak_bridges=False)
        groups = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(len(groups), 3)
        self._assert_partition(groups, range(1, 4))

    def test_isolated_clusters_absorbed_by_file_overlap(self):
        # No inter-cluster edges at all; absorption falls back to file overlap.
        clusters = {cid: {f"n{cid}"} for cid in range(1, 21)}
        # Four clusters share each of five files, so overlap can regroup them.
        cluster_to_files = {cid: {f"/repo/dir{(cid - 1) // 4}.py"} for cid in range(1, 21)}
        file_to_clusters: dict[str, set[int]] = {}
        for cid, files in cluster_to_files.items():
            for f in files:
                file_to_clusters.setdefault(f, set()).add(cid)
        cr = ClusterResult(
            clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
        )
        graph = nx.DiGraph()
        for cid in range(1, 21):
            graph.add_node(f"n{cid}", file_path=next(iter(cluster_to_files[cid])))
        groups = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(len(groups), TOP_LEVEL_COMPONENTS_MIN)
        self._assert_partition(groups, range(1, 21))

    def test_large_isolated_cluster_becomes_its_own_component(self):
        # A connected core plus one big, call-isolated module (e.g. a data-model
        # file nothing calls). The big module must stay its own component, not be
        # folded into a seed.
        cr, graph = self._blocks(n_blocks=4, per_block=3)
        big_id = 999
        cr.clusters[big_id] = {f"models.Model{i}" for i in range(60)}  # 60 methods, no call edges
        cr.cluster_to_files[big_id] = {"/repo/models/schema.py"}
        cr.file_to_clusters["/repo/models/schema.py"] = {big_id}
        graph.add_node("models.Model0", file_path="/repo/models/schema.py")

        groups = supercluster_by_modularity_peak(cr, graph)
        owner = next(group for group in groups if big_id in group)
        # It seeded its own group rather than being absorbed into a larger one.
        self.assertEqual(owner, {big_id})

    def test_leaf_ids_combines_languages_into_single_budget(self):
        cr_py, graph = self._blocks(n_blocks=6, per_block=5)
        # Split the same clusters/graph across two languages by cluster id.
        py_clusters = {cid: cr_py.clusters[cid] for cid in range(1, 16)}
        js_clusters = {cid: cr_py.clusters[cid] for cid in range(16, 31)}
        py = ClusterResult(clusters=py_clusters, cluster_to_files=cr_py.cluster_to_files, strategy="t")
        js = ClusterResult(clusters=js_clusters, cluster_to_files=cr_py.cluster_to_files, strategy="t")
        groups = supercluster_leaf_ids({"python": py, "javascript": js}, {"python": graph, "javascript": graph})
        self.assertTrue(TOP_LEVEL_COMPONENTS_MIN <= len(groups) <= TOP_LEVEL_COMPONENTS_MAX)
        self._assert_partition(groups, range(1, 31))


class TestSubgraphPeakModularity(unittest.TestCase):
    @staticmethod
    def _cr_and_graph(clusters, edges):
        """A ClusterResult (one node per leaf cluster) + a DiGraph with the given cluster-id edges."""
        cluster_map = {cid: {f"n{cid}"} for cid in clusters}
        cluster_to_files = {cid: {f"/repo/c{cid}.py"} for cid in clusters}
        file_to_clusters = {f"/repo/c{cid}.py": {cid} for cid in clusters}
        graph = nx.DiGraph()
        for cid in clusters:
            graph.add_node(f"n{cid}", file_path=f"/repo/c{cid}.py")
        for s, d in edges:
            graph.add_edge(f"n{s}", f"n{d}")
        cr = ClusterResult(
            clusters=cluster_map, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
        )
        return {"python": cr}, {"python": graph}

    def test_separable_structure_scores_high(self):
        # Three tight triangles, weakly bridged -> clean community split -> high modularity.
        edges = [(1, 2), (2, 3), (3, 1), (4, 5), (5, 6), (6, 4), (7, 8), (8, 9), (9, 7), (1, 4), (4, 7)]
        crs, cfgs = self._cr_and_graph(range(1, 10), edges)
        self.assertGreater(subgraph_peak_modularity(crs, cfgs), 0.25)

    def test_cohesive_blob_scores_low(self):
        # A single fully-connected clique has no internal boundary -> ~0 modularity.
        ids = range(1, 7)
        edges = [(s, d) for s in ids for d in ids if s != d]
        crs, cfgs = self._cr_and_graph(ids, edges)
        self.assertLess(subgraph_peak_modularity(crs, cfgs), 0.25)

    def test_no_edges_scores_zero(self):
        crs, cfgs = self._cr_and_graph(range(1, 6), [])
        self.assertEqual(subgraph_peak_modularity(crs, cfgs), 0.0)

    def test_single_cluster_scores_zero(self):
        crs, cfgs = self._cr_and_graph([1], [])
        self.assertEqual(subgraph_peak_modularity(crs, cfgs), 0.0)


if __name__ == "__main__":
    unittest.main()
