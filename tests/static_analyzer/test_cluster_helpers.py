import unittest
from unittest.mock import MagicMock, patch

import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import (
    build_all_cluster_results,
    enforce_cross_language_budget,
    reindex_cluster_result,
    MAX_LLM_CLUSTERS,
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


if __name__ == "__main__":
    unittest.main()
