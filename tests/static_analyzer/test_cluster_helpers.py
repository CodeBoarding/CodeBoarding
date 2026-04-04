import unittest
from unittest.mock import MagicMock, patch

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results
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


if __name__ == "__main__":
    unittest.main()
