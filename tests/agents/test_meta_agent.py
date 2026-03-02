import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import MetaAnalysisInsights
from agents.meta_agent import MetaAgent
from caching.meta_cache import MetaCache
from utils import get_cache_dir


class TestMetaAgent(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "pyproject.toml").write_text('[project]\nname = "test-repo"\n', encoding="utf-8")
        self.project_name = "test_project"

    def tearDown(self):
        import shutil

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
        llm.max_tokens = 1024
        llm.max_retries = 2
        llm.timeout = 30.0
        llm.base_url = None
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
        self.assertIsNotNone(agent._meta_cache)

        expected_cache_file = get_cache_dir(self.repo_dir) / "meta_agent_llm.sqlite"
        self.assertEqual(agent._meta_cache.file_path, expected_cache_file)

    @patch("agents.meta_agent.MetaAgent._parse_invoke")
    def test_analyze_project_metadata_cache_hit(self, mock_parse_invoke):
        agent = self._build_agent()
        first = self._meta_insights("library")
        mock_parse_invoke.return_value = first

        loaded_first = agent.analyze_project_metadata(skip_cache=False)
        loaded_second = agent.analyze_project_metadata(skip_cache=False)

        self.assertEqual(mock_parse_invoke.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), first.model_dump())

    @patch("agents.meta_agent.MetaAgent._parse_invoke")
    def test_analyze_project_metadata_skip_cache_forces_recompute(self, mock_parse_invoke):
        agent = self._build_agent()
        first = self._meta_insights("library")
        second = self._meta_insights("web application")
        mock_parse_invoke.side_effect = [first, second]

        loaded_first = agent.analyze_project_metadata(skip_cache=True)
        loaded_second = agent.analyze_project_metadata(skip_cache=True)

        self.assertEqual(mock_parse_invoke.call_count, 2)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())

    def test_parsing_model_change_invalidates_cache(self):
        shared_agent_llm = self._mock_llm("meta-model-v1")

        agent_a = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=shared_agent_llm,
            parsing_llm=self._mock_llm("parse-model-v1"),
        )
        agent_b = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=shared_agent_llm,
            parsing_llm=self._mock_llm("parse-model-v2"),
        )

        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch.object(agent_a, "_parse_invoke", return_value=first) as parse_a,
            patch.object(agent_b, "_parse_invoke", return_value=second) as parse_b,
        ):
            loaded_first = agent_a.analyze_project_metadata(skip_cache=False)
            loaded_second = agent_b.analyze_project_metadata(skip_cache=False)

        self.assertEqual(parse_a.call_count, 1)
        self.assertEqual(parse_b.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())

    def test_discover_metadata_files_includes_manifest_and_readme(self):
        agent = self._build_agent()
        (self.repo_dir / "README.md").write_text("# test\n", encoding="utf-8")
        (self.repo_dir / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")

        with patch.object(agent._meta_cache._ignore_manager, "should_ignore", return_value=False):
            files = [p.as_posix() for p in agent._meta_cache.discover_metadata_files()]

        self.assertIn("pyproject.toml", files)
        self.assertIn("README.md", files)
        self.assertIn("setup.py", files)

    def test_compute_metadata_hash_empty_is_stable(self):
        cache = MetaCache(repo_dir=self.repo_dir, ignore_manager=self._build_agent().ignore_manager)
        expected = hashlib.sha256(b"").hexdigest()
        self.assertEqual(cache._compute_metadata_content_hash([]), expected)

    @patch("agents.meta_agent.MetaAgent._parse_invoke")
    def test_meta_cache_stores_non_empty_run_id(self, mock_parse_invoke):
        agent = self._build_agent()
        mock_parse_invoke.return_value = self._meta_insights("library")

        agent.analyze_project_metadata(skip_cache=True)

        latest = agent._meta_cache.load_most_recent_run(namespace="__default__")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest[0], "meta")


if __name__ == "__main__":
    unittest.main()
