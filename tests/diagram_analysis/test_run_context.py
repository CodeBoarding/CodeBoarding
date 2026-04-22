import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_responses import AnalysisInsights, ClusterAnalysis
from caching.cache import ModelSettings
from caching.details_cache import ClusterCache, FinalAnalysisCache
from diagram_analysis.run_context import RunContext, _load_existing_run_id


class TestRunContext(unittest.TestCase):
    @patch("diagram_analysis.run_context.generate_log_path", return_value="project/2026-03-18_09-00-00")
    @patch("diagram_analysis.run_context.generate_run_id", return_value="fresh-run-id")
    def test_resolve_generates_fresh_run_by_default(self, mock_generate_run_id, mock_generate_log_path):
        result = RunContext.resolve(
            repo_dir=Path("/tmp/repo"),
            project_name="project",
        )

        self.assertEqual(result.run_id, "fresh-run-id")
        self.assertEqual(result.log_path, "project/2026-03-18_09-00-00")
        self.assertEqual(result.repo_dir, Path("/tmp/repo"))
        mock_generate_run_id.assert_called_once_with()
        mock_generate_log_path.assert_called_once_with("project")

    @patch("diagram_analysis.run_context.generate_log_path", return_value="project/2026-03-18_09-00-00")
    @patch("diagram_analysis.run_context.generate_run_id", return_value="fresh-run-id")
    @patch("diagram_analysis.run_context._load_existing_run_id", return_value="cached-run-id")
    def test_resolve_reuses_existing_run_id_when_requested(
        self,
        mock_load_existing_run_id,
        mock_generate_run_id,
        mock_generate_log_path,
    ):
        result = RunContext.resolve(
            repo_dir=Path("/tmp/repo"),
            project_name="project",
            reuse_latest_run_id=True,
        )

        self.assertEqual(result.run_id, "cached-run-id")
        self.assertEqual(result.log_path, "project/2026-03-18_09-00-00")
        mock_load_existing_run_id.assert_called_once_with(Path("/tmp/repo"))
        mock_generate_run_id.assert_not_called()
        mock_generate_log_path.assert_called_once_with("project")

    @patch("diagram_analysis.run_context.generate_log_path", return_value="project/2026-03-18_09-00-00")
    @patch("diagram_analysis.run_context.generate_run_id", return_value="fresh-run-id")
    @patch("diagram_analysis.run_context._load_existing_run_id", return_value=None)
    def test_resolve_falls_back_to_fresh_run_id(
        self,
        mock_load_existing_run_id,
        mock_generate_run_id,
        mock_generate_log_path,
    ):
        result = RunContext.resolve(
            repo_dir=Path("/tmp/repo"),
            project_name="project",
            reuse_latest_run_id=True,
        )

        self.assertEqual(result.run_id, "fresh-run-id")
        self.assertEqual(result.log_path, "project/2026-03-18_09-00-00")
        mock_load_existing_run_id.assert_called_once_with(Path("/tmp/repo"))
        mock_generate_run_id.assert_called_once_with()
        mock_generate_log_path.assert_called_once_with("project")

    @patch("diagram_analysis.run_context.prune_details_caches")
    def test_finalize_prunes_to_single_run_id(self, mock_prune_details_caches):
        ctx = RunContext(run_id="run-123", log_path="project/ts", repo_dir=Path("/tmp/repo"))
        ctx.finalize()

        mock_prune_details_caches.assert_called_once_with(
            repo_dir=Path("/tmp/repo"),
            only_keep_run_id="run-123",
        )


class TestLoadExistingRunId(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        (self.repo_dir / ".git").mkdir()
        self.model_settings = ModelSettings(provider="test", chat_class="TestChat", model_name="test-model")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _analysis(self, description: str) -> AnalysisInsights:
        return AnalysisInsights(description=description, components=[], components_relations=[])

    def test_prefers_most_recent_over_lexicographic_order(self):
        final_cache = FinalAnalysisCache(self.repo_dir)

        old_run_id = "0" * 32
        new_run_id = "f" * 32

        old_key = final_cache.build_key("prompt-old", self.model_settings)
        new_key = final_cache.build_key("prompt-new", self.model_settings)

        final_cache.store(old_key, self._analysis("old"), run_id=old_run_id)
        time.sleep(0.001)
        final_cache.store(new_key, self._analysis("new"), run_id=new_run_id)

        self.assertEqual(_load_existing_run_id(self.repo_dir), new_run_id)

    def test_uses_latest_timestamp_across_both_caches(self):
        final_cache = FinalAnalysisCache(self.repo_dir)
        cluster_cache = ClusterCache(self.repo_dir)

        final_run_id = "a" * 32
        cluster_run_id = "b" * 32

        final_key = final_cache.build_key("final", self.model_settings)
        cluster_key = cluster_cache.build_key("cluster", self.model_settings)

        final_cache.store(final_key, self._analysis("final"), run_id=final_run_id)
        time.sleep(0.001)
        cluster_cache.store(cluster_key, ClusterAnalysis(cluster_components=[]), run_id=cluster_run_id)

        self.assertEqual(_load_existing_run_id(self.repo_dir), cluster_run_id)


if __name__ == "__main__":
    unittest.main()
