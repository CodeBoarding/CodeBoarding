import unittest
from unittest.mock import MagicMock

import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import (
    TOP_LEVEL_COMPONENTS_MAX,
    TOP_LEVEL_COMPONENTS_MIN,
    build_all_cluster_results,
    reindex_across_languages,
    reindex_cluster_result,
    supercluster_by_modularity_peak,
    supercluster_leaf_ids,
)
from static_analyzer.graph import ClusterResult


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


class TestClusterHelpers(unittest.TestCase):
    def test_multi_tech_stack_cluster_ids_are_reindexed_without_overlap(self):
        analysis = MagicMock(spec=StaticAnalysisResults)
        analysis.get_languages.return_value = ["python", "typescript"]

        python_cfg = MagicMock()
        typescript_cfg = MagicMock()
        python_cfg.cluster.return_value = _make_cluster_result("py", 40)
        typescript_cfg.cluster.return_value = _make_cluster_result("ts", 40)
        analysis.get_cfg.side_effect = lambda language: {
            "python": python_cfg,
            "typescript": typescript_cfg,
        }[language]

        result = build_all_cluster_results(analysis)

        python_ids = set(result["python"].clusters.keys())
        typescript_ids = set(result["typescript"].clusters.keys())
        self.assertEqual(python_ids, set(range(1, 41)))
        self.assertTrue(python_ids.isdisjoint(typescript_ids))
        self.assertEqual(len(typescript_ids), 40)

        shifted_ts_ids = set().union(*result["typescript"].file_to_clusters.values())
        self.assertEqual(shifted_ts_ids, typescript_ids)
        self.assertIs(python_cfg._cluster_cache, result["python"])
        self.assertIs(typescript_cfg._cluster_cache, result["typescript"])

    def test_all_clusters_survive_grouping(self):
        """Every leaf cluster keeps its members — nothing is merged away before grouping."""
        analysis = MagicMock(spec=StaticAnalysisResults)
        analysis.get_languages.return_value = ["python"]
        cfg = MagicMock()
        cfg.cluster.return_value = _make_cluster_result("py", 120)
        analysis.get_cfg.return_value = cfg

        result = build_all_cluster_results(analysis)

        self.assertEqual(len(result["python"].clusters), 120)

    def test_reindex_cluster_result_shifts_all_ids(self):
        shifted = reindex_cluster_result(_make_cluster_result("x", 3), 10)

        self.assertEqual(set(shifted.clusters.keys()), {11, 12, 13})
        self.assertEqual(set(shifted.cluster_to_files.keys()), {11, 12, 13})
        for file_ids in shifted.file_to_clusters.values():
            self.assertTrue(file_ids.issubset({11, 12, 13}))

    def test_reindex_across_languages_makes_ids_disjoint(self):
        cluster_results = {
            "javascript": _make_cluster_result("js", 10),
            "python": _make_cluster_result("py", 10),
        }

        reindex_across_languages(cluster_results)

        js_ids = set(cluster_results["javascript"].clusters.keys())
        py_ids = set(cluster_results["python"].clusters.keys())
        self.assertTrue(js_ids.isdisjoint(py_ids), f"Overlap detected: {js_ids & py_ids}")
        self.assertEqual(len(js_ids) + len(py_ids), 20)

    def test_reindex_across_languages_noop_for_single_language(self):
        cr = _make_cluster_result("py", 10)
        cluster_results = {"python": cr}

        reindex_across_languages(cluster_results)

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
        groups, modularity = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(len(groups), 6)
        self.assertGreater(modularity, 0.5)
        self._assert_partition(groups, range(1, 31))

    def test_count_is_clamped_to_range(self):
        # 10 natural blocks, but the count must not exceed the max.
        cr, graph = self._blocks(n_blocks=10, per_block=4)
        groups, _modularity = supercluster_by_modularity_peak(cr, graph)
        self.assertTrue(TOP_LEVEL_COMPONENTS_MIN <= len(groups) <= TOP_LEVEL_COMPONENTS_MAX)
        self._assert_partition(groups, range(1, 41))

    def test_deterministic_across_runs(self):
        cr, graph = self._blocks(n_blocks=7, per_block=4)
        first, first_q = supercluster_by_modularity_peak(cr, graph)
        second, second_q = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(sorted(map(sorted, first)), sorted(map(sorted, second)))
        self.assertEqual(first_q, second_q)

    def test_fewer_clusters_than_floor_returns_singletons(self):
        cr, graph = self._blocks(n_blocks=1, per_block=3, weak_bridges=False)
        groups, _modularity = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(len(groups), 3)
        self._assert_partition(groups, range(1, 4))

    def test_isolated_clusters_are_spread_not_piled_onto_one_seed(self):
        # No inter-cluster edges at all: every cluster is a leftover, so only directory
        # affinity and seed size can place them. The old file-overlap rule grew one seed's
        # package set as it absorbed, which made that seed win every later comparison and
        # swallow the repo.
        clusters = {cid: {f"n{cid}"} for cid in range(1, 41)}
        cluster_to_files = {cid: {f"/repo/dir{(cid - 1) // 8}/m{cid}.py"} for cid in range(1, 41)}
        file_to_clusters: dict[str, set[int]] = {}
        for cid, files in cluster_to_files.items():
            for path in files:
                file_to_clusters.setdefault(path, set()).add(cid)
        cr = ClusterResult(
            clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
        )
        graph = nx.DiGraph()
        for cid in range(1, 41):
            graph.add_node(f"n{cid}", file_path=next(iter(cluster_to_files[cid])))

        groups, _modularity = supercluster_by_modularity_peak(cr, graph)

        self.assertEqual(len(groups), TOP_LEVEL_COMPONENTS_MIN)
        self._assert_partition(groups, range(1, 41))
        biggest = max(len(group) for group in groups)
        self.assertLessEqual(biggest, 12, f"one seed absorbed {biggest}/40 clusters: {sorted(map(len, groups))}")

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

        groups, _modularity = supercluster_by_modularity_peak(cr, graph)
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
        groups, _modularity = supercluster_leaf_ids(
            {"python": py, "javascript": js}, {"python": graph, "javascript": graph}
        )
        self.assertTrue(TOP_LEVEL_COMPONENTS_MIN <= len(groups) <= TOP_LEVEL_COMPONENTS_MAX)
        self._assert_partition(groups, range(1, 31))

    def test_modularity_is_zero_without_inter_cluster_edges(self):
        cr, graph = self._blocks(n_blocks=6, per_block=5, weak_bridges=False)
        # Strip every edge so the meta-graph has nothing to separate.
        graph = nx.DiGraph()
        for cid, members in cr.clusters.items():
            for member in members:
                graph.add_node(member, file_path=next(iter(cr.cluster_to_files[cid])))
        _groups, modularity = supercluster_by_modularity_peak(cr, graph)
        self.assertEqual(modularity, 0.0)


if __name__ == "__main__":
    unittest.main()
