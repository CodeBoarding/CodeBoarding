import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_community.cache import SQLiteCache
from langchain_core.outputs import Generation

from agents.agent_responses import MetaAnalysisInsights
from agents.meta_agent import MetaAgent
from caching.meta_cache import MetaCacheRecord
from static_analyzer.analysis_result import StaticAnalysisResults
from utils import get_cache_dir


class TestMetaAgent(unittest.TestCase):
    def setUp(self):
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]

        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "pyproject.toml").write_text('[project]\nname = "test-repo"\n', encoding="utf-8")
        self.project_name = "test_project"

    def tearDown(self):
        import shutil

        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _meta_insights(self, project_type: str) -> MetaAnalysisInsights:
        return MetaAnalysisInsights(
            project_type=project_type,
            domain="software development",
            architectural_patterns=["modular architecture"],
            expected_components=["core", "testing"],
            technology_stack=["Python", "pytest"],
            architectural_bias="Focus on modularity and testability",
        )

    def _mock_llm(self, model_name: str) -> MagicMock:
        llm = MagicMock()
        llm.model_name = model_name
        return llm

    def _build_agent(self, model_name: str = "meta-model-v1", parser_name: str = "parse-model-v1") -> MetaAgent:
        return MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=self._mock_llm(model_name),
            parsing_llm=self._mock_llm(parser_name),
        )

    def test_init(self):
        agent = self._build_agent()
        self.assertEqual(agent.project_name, self.project_name)
        self.assertIsNotNone(agent.meta_analysis_prompt)
        self.assertIsNotNone(agent.agent)
        self.assertIsNotNone(agent._cache)
        expected_cache_file = get_cache_dir(self.repo_dir) / "meta_agent_llm.sqlite"
        self.assertEqual(agent._cache.file_path, expected_cache_file)

    @patch("agents.meta_agent.MetaAgent._parse_invoke")
    def test_analyze_project_metadata(self, mock_parse_invoke):
        agent = self._build_agent()
        mock_meta_insights = self._meta_insights("library")
        mock_parse_invoke.return_value = mock_meta_insights

        result = agent.analyze_project_metadata()

        self.assertEqual(result, mock_meta_insights)
        self.assertEqual(result.project_type, "library")
        self.assertEqual(result.domain, "software development")
        self.assertEqual(len(result.technology_stack), 2)
        self.assertIn("Python", result.technology_stack)
        mock_parse_invoke.assert_called_once()

    @patch("agents.meta_agent.MetaAgent._parse_invoke")
    def test_analyze_project_metadata_application(self, mock_parse_invoke):
        agent = self._build_agent()
        mock_meta_insights = MetaAnalysisInsights(
            project_type="web application",
            domain="web development",
            architectural_patterns=["REST API", "MVC"],
            expected_components=["API", "Database", "Models"],
            technology_stack=["Python", "FastAPI", "PostgreSQL"],
            architectural_bias="Focus on scalable web architecture",
        )
        mock_parse_invoke.return_value = mock_meta_insights

        result = agent.analyze_project_metadata()

        self.assertEqual(result, mock_meta_insights)
        self.assertEqual(result.project_type, "web application")
        self.assertEqual(result.domain, "web development")
        self.assertEqual(len(result.expected_components), 3)
        mock_parse_invoke.assert_called_once()

    def test_get_meta_context_cache_miss_then_hit(self):
        agent = self._build_agent()
        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_git_commit_hash", return_value="commit-a"),
            patch.object(agent._cache, "discover_watch_files", return_value=["pyproject.toml"]),
            patch.object(agent._cache, "is_stale", return_value=False) as is_stale_mock,
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(is_stale_mock.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), first.model_dump())

    def test_get_meta_context_invalidates_when_watch_files_change(self):
        agent = self._build_agent()
        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_git_commit_hash", side_effect=["commit-a", "commit-b"]),
            patch.object(agent._cache, "discover_watch_files", side_effect=[["pyproject.toml"], ["pyproject.toml"]]),
            patch.object(agent._cache, "is_stale", return_value=True) as is_stale_mock,
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 2)
        self.assertEqual(is_stale_mock.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())

    def test_get_meta_context_refresh_recomputes(self):
        agent = self._build_agent()
        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_git_commit_hash", side_effect=["commit-a", "commit-b"]),
            patch.object(agent._cache, "discover_watch_files", side_effect=[["pyproject.toml"], ["pyproject.toml"]]),
            patch.object(agent._cache, "is_stale", return_value=False),
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context(refresh=True)
            loaded_third = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 2)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())
        self.assertEqual(loaded_third.model_dump(), second.model_dump())

    def test_get_meta_context_corrupt_cache_falls_back_to_recompute(self):
        agent = self._build_agent()
        prompt_key = agent._cache._prompt_key
        llm_key = agent._cache._llm_key
        cache = SQLiteCache(database_path=str(agent._cache.file_path))
        cache.update(prompt_key, llm_key, [Generation(text="not-json")])

        recomputed = self._meta_insights("library")
        with (
            patch("agents.meta_agent.get_git_commit_hash", return_value="commit-a"),
            patch.object(agent._cache, "discover_watch_files", return_value=["pyproject.toml"]),
            patch.object(agent, "analyze_project_metadata", return_value=recomputed) as analyze_mock,
        ):
            loaded = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(loaded.model_dump(), recomputed.model_dump())

    def test_get_meta_context_legacy_payload_is_cache_miss(self):
        agent = self._build_agent()
        prompt_key = agent._cache._prompt_key
        llm_key = agent._cache._llm_key
        cache = SQLiteCache(database_path=str(agent._cache.file_path))

        legacy_meta = self._meta_insights("legacy")
        cache.update(prompt_key, llm_key, [Generation(text=legacy_meta.model_dump_json())])

        recomputed = self._meta_insights("library")
        with (
            patch("agents.meta_agent.get_git_commit_hash", return_value="commit-a"),
            patch.object(agent._cache, "discover_watch_files", return_value=["pyproject.toml"]),
            patch.object(agent, "analyze_project_metadata", return_value=recomputed) as analyze_mock,
        ):
            loaded = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(loaded.model_dump(), recomputed.model_dump())

    def test_get_meta_context_cache_init_failure_falls_back_to_recompute(self):
        agent = self._build_agent()
        recomputed = self._meta_insights("library")

        with (
            patch.object(agent._cache, "_open_sqlite", return_value=None),
            patch.object(agent, "analyze_project_metadata", return_value=recomputed) as analyze_mock,
        ):
            loaded = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(loaded.model_dump(), recomputed.model_dump())

    def test_get_meta_context_invalidates_when_parsing_model_changes(self):
        agent_llm = self._mock_llm("meta-model-v1")
        parsing_llm_a = self._mock_llm("parse-model-v1")
        parsing_llm_b = self._mock_llm("parse-model-v2")

        agent_a = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm_a,
        )
        agent_b = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm_b,
        )

        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_git_commit_hash", return_value="commit-a"),
            patch.object(agent_a._cache, "discover_watch_files", return_value=["pyproject.toml"]),
            patch.object(agent_b._cache, "discover_watch_files", return_value=["pyproject.toml"]),
            patch.object(agent_a, "analyze_project_metadata", return_value=first) as analyze_a,
            patch.object(agent_b, "analyze_project_metadata", return_value=second) as analyze_b,
        ):
            loaded_first = agent_a.get_meta_context()
            loaded_second = agent_b.get_meta_context()

        self.assertEqual(analyze_a.call_count, 1)
        self.assertEqual(analyze_b.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())

    def test_get_meta_context_uses_cache_when_gitpython_unavailable(self):
        agent = self._build_agent()
        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("repo_utils.GIT_AVAILABLE", False),
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), first.model_dump())

    def test_discover_watch_files_includes_manifests_and_configs(self):
        agent = self._build_agent()
        (self.repo_dir / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
        (self.repo_dir / "tsconfig.json").write_text("{}\n", encoding="utf-8")

        with patch.object(agent._cache._ignore_manager, "should_ignore", return_value=False):
            watch = agent._cache.discover_watch_files()

        self.assertIn("setup.py", watch)
        self.assertIn("tsconfig.json", watch)

    def test_discover_watch_files_excludes_lock_files(self):
        agent = self._build_agent()
        (self.repo_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (self.repo_dir / "uv.lock").write_text("...", encoding="utf-8")
        (self.repo_dir / "poetry.lock").write_text("...", encoding="utf-8")

        with patch.object(agent._cache._ignore_manager, "should_ignore", return_value=False):
            watch = agent._cache.discover_watch_files()

        self.assertIn("pyproject.toml", watch)
        self.assertNotIn("uv.lock", watch)
        self.assertNotIn("poetry.lock", watch)

    def test_discover_watch_files_includes_readme(self):
        agent = self._build_agent()
        (self.repo_dir / "README.md").write_text("# My Project\n", encoding="utf-8")

        with patch.object(agent._cache._ignore_manager, "should_ignore", return_value=False):
            watch = agent._cache.discover_watch_files()

        self.assertIn("README.md", watch)

    def test_discover_watch_files_includes_untracked_watch_files(self):
        agent = self._build_agent()
        (self.repo_dir / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
        (self.repo_dir / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
        (self.repo_dir / "README.md").write_text("# My Project\n", encoding="utf-8")

        with patch.object(agent._cache._ignore_manager, "should_ignore", return_value=False):
            watch = agent._cache.discover_watch_files()

        self.assertIn("setup.py", watch)
        self.assertIn("package.json", watch)
        self.assertIn("README.md", watch)
        self.assertNotIn("notes.txt", watch)

    def test_is_stale_returns_false_for_empty_watch_list(self):
        agent = self._build_agent()
        record = MetaCacheRecord(meta=self._meta_insights("library"), base_commit="commit-a", watch_files=[])
        self.assertFalse(agent._cache.is_stale(record))

    def test_is_stale_returns_false_for_matching_watch_fingerprint(self):
        agent = self._build_agent()
        watch_files = ["pyproject.toml"]
        watch_state_hash = agent._cache._compute_metadata_content_hash(watch_files)
        self.assertIsNotNone(watch_state_hash)

        record = MetaCacheRecord(
            meta=self._meta_insights("library"),
            base_commit="commit-a",
            watch_files=watch_files,
            watch_state_hash=watch_state_hash,
        )
        self.assertFalse(agent._cache.is_stale(record))

    def test_is_stale_returns_true_for_legacy_record_without_fingerprint(self):
        agent = self._build_agent()
        record = MetaCacheRecord(
            meta=self._meta_insights("library"), base_commit="commit-a", watch_files=["pyproject.toml"]
        )
        self.assertTrue(agent._cache.is_stale(record))

    def test_is_stale_detects_watch_file_content_change(self):
        agent = self._build_agent()
        watch_files = ["pyproject.toml"]
        watch_state_hash = agent._cache._compute_metadata_content_hash(watch_files)
        self.assertIsNotNone(watch_state_hash)

        record = MetaCacheRecord(
            meta=self._meta_insights("library"),
            base_commit="commit-a",
            watch_files=watch_files,
            watch_state_hash=watch_state_hash,
        )
        (self.repo_dir / "pyproject.toml").write_text('[project]\nname = "changed"\n', encoding="utf-8")
        self.assertTrue(agent._cache.is_stale(record))

    def test_is_stale_detects_watch_file_set_change(self):
        agent = self._build_agent()
        watch_files = ["pyproject.toml"]
        watch_state_hash = agent._cache._compute_metadata_content_hash(watch_files)
        self.assertIsNotNone(watch_state_hash)

        record = MetaCacheRecord(
            meta=self._meta_insights("library"),
            base_commit="commit-a",
            watch_files=watch_files,
            watch_state_hash=watch_state_hash,
        )
        with patch.object(agent._cache, "discover_watch_files", return_value=["pyproject.toml", "README.md"]):
            self.assertTrue(agent._cache.is_stale(record))

    def test_is_stale_returns_true_when_watch_file_missing(self):
        agent = self._build_agent()
        watch_files = ["pyproject.toml"]
        watch_state_hash = agent._cache._compute_metadata_content_hash(watch_files)
        self.assertIsNotNone(watch_state_hash)

        record = MetaCacheRecord(
            meta=self._meta_insights("library"),
            base_commit="commit-a",
            watch_files=watch_files,
            watch_state_hash=watch_state_hash,
        )
        (self.repo_dir / "pyproject.toml").unlink()
        self.assertTrue(agent._cache.is_stale(record))

    def test_get_meta_context_stores_when_commit_hash_unavailable(self):
        agent = self._build_agent()
        recomputed = self._meta_insights("library")
        with (
            patch("agents.meta_agent.get_git_commit_hash", return_value="NoCommitHash"),
            patch.object(agent._cache, "discover_watch_files", return_value=["pyproject.toml"]),
            patch.object(agent, "analyze_project_metadata", return_value=recomputed),
            patch.object(agent._cache, "store") as store_mock,
        ):
            loaded = agent.get_meta_context()

        self.assertEqual(loaded.model_dump(), recomputed.model_dump())
        store_mock.assert_called_once()
        saved_record = store_mock.call_args.args[0]
        self.assertEqual(saved_record.base_commit, "NoCommitHash")
        self.assertIsNotNone(saved_record.watch_state_hash)


if __name__ == "__main__":
    unittest.main()
