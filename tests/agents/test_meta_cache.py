import shutil
import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import MetaAnalysisInsights
from agents.meta_cache import MetaAgentCache, MetaSnapshot


class TestMetaAgentCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.cache = MetaAgentCache.from_repo_dir(self.repo_dir)

        self.result = MetaAnalysisInsights(
            project_type="library",
            domain="software",
            architectural_patterns=["modular"],
            expected_components=["core"],
            technology_stack=["python"],
            architectural_bias="Prefer module boundaries",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_if_valid_allows_small_docs_change(self):
        base_docs = " ".join(f"word{i}" for i in range(1000))
        typo_docs = base_docs.replace("word500", "word50O")

        first = MetaSnapshot(
            scope=str(self.repo_dir.resolve()),
            docs_text=base_docs,
            deps_hash="deps_v1",
            tree_hash="tree_v1",
            model_id="model_v1",
            prompt_version="prompt_v1",
        )
        self.cache.save(first, self.result)

        second = MetaSnapshot(
            scope=str(self.repo_dir.resolve()),
            docs_text=typo_docs,
            deps_hash="deps_v1",
            tree_hash="tree_v1",
            model_id="model_v1",
            prompt_version="prompt_v1",
        )
        loaded = self.cache.load_if_valid(second)

        self.assertIsNotNone(loaded)
        if loaded is None:
            return
        self.assertEqual(loaded.project_type, self.result.project_type)

    def test_load_if_valid_invalidates_on_deps_change(self):
        first = MetaSnapshot(
            scope=str(self.repo_dir.resolve()),
            docs_text="README",
            deps_hash="deps_v1",
            tree_hash="tree_v1",
            model_id="model_v1",
            prompt_version="prompt_v1",
        )
        self.cache.save(first, self.result)

        changed_deps = MetaSnapshot(
            scope=str(self.repo_dir.resolve()),
            docs_text="README",
            deps_hash="deps_v2",
            tree_hash="tree_v1",
            model_id="model_v1",
            prompt_version="prompt_v1",
        )
        loaded = self.cache.load_if_valid(changed_deps)
        self.assertIsNone(loaded)


if __name__ == "__main__":
    unittest.main()
