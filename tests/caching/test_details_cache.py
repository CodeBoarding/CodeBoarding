import tempfile
import time
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, ClusterAnalysis
from caching.cache import ModelSettings
from caching.details_cache import ClusterCache, FinalAnalysisCache, _load_existing_run_id


class TestDetailsCacheRunIdSelection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.model_settings = ModelSettings(provider="test", chat_class="TestChat", model_name="test-model")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _analysis(self, description: str) -> AnalysisInsights:
        return AnalysisInsights(description=description, components=[], components_relations=[])

    def test_load_existing_run_id_prefers_most_recent_over_lexicographic_order(self):
        final_cache = FinalAnalysisCache(self.repo_dir)

        old_run_id = "0" * 32
        new_run_id = "f" * 32

        old_key = final_cache.build_key("prompt-old", self.model_settings)
        new_key = final_cache.build_key("prompt-new", self.model_settings)

        final_cache.store(old_key, self._analysis("old"), run_id=old_run_id)
        time.sleep(0.001)
        final_cache.store(new_key, self._analysis("new"), run_id=new_run_id)

        self.assertEqual(_load_existing_run_id(self.repo_dir), new_run_id)

    def test_load_existing_run_id_uses_latest_timestamp_across_both_caches(self):
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
