import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_community.cache import SQLiteCache
from langchain_core.outputs import Generation

from agents.meta_agent import MetaAgent
from agents.agent_responses import MetaAnalysisInsights
from static_analyzer.analysis_result import StaticAnalysisResults


class TestMetaAgent(unittest.TestCase):
    def setUp(self):
        # Create mock static analysis
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]

        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
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

    def test_init(self):
        # Test initialization
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertIsNotNone(agent.meta_analysis_prompt)
        self.assertIsNotNone(agent.agent)

    @patch("agents.meta_agent.MetaAgent._parse_invoke")
    def test_analyze_project_metadata(self, mock_parse_invoke):
        # Test analyze_project_metadata
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        mock_meta_insights = MetaAnalysisInsights(
            project_type="library",
            domain="software development",
            architectural_patterns=["modular architecture"],
            expected_components=["core", "testing"],
            technology_stack=["Python", "pytest"],
            architectural_bias="Focus on modularity and testability",
        )
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
        # Test analyze_project_metadata for an application
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

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
        mock_llm = self._mock_llm("meta-model-v1")
        mock_parsing_llm = MagicMock()
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_repo_state_hash", return_value="state-a"),
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), first.model_dump())

    def test_get_meta_context_invalidates_when_repo_state_hash_changes(self):
        mock_llm = self._mock_llm("meta-model-v1")
        mock_parsing_llm = MagicMock()
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_repo_state_hash", side_effect=["state-a", "state-b"]),
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 2)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())

    def test_get_meta_context_force_refresh_recomputes(self):
        mock_llm = self._mock_llm("meta-model-v1")
        mock_parsing_llm = MagicMock()
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_repo_state_hash", return_value="state-a"),
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context(force_refresh=True)
            loaded_third = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 2)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())
        self.assertEqual(loaded_third.model_dump(), second.model_dump())

    def test_get_meta_context_corrupt_cache_falls_back_to_recompute(self):
        mock_llm = self._mock_llm("meta-model-v1")
        mock_parsing_llm = self._mock_llm("parse-model-v1")
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        with patch("agents.meta_agent.get_repo_state_hash", return_value="state-a"):
            prompt_key = agent._meta_cache_prompt()
            llm_string = agent._meta_cache_llm_string(mock_llm, mock_parsing_llm)
            cache = SQLiteCache(database_path=str(agent._meta_cache_path()))
            cache.update(prompt_key, llm_string, [Generation(text="not-json")])

            recomputed = self._meta_insights("library")
            with patch.object(agent, "analyze_project_metadata", return_value=recomputed) as analyze_mock:
                loaded = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 1)
        self.assertEqual(loaded.model_dump(), recomputed.model_dump())

    def test_get_meta_context_non_git_repo_bypasses_cache(self):
        mock_llm = self._mock_llm("meta-model-v1")
        mock_parsing_llm = self._mock_llm("parse-model-v1")
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        first = self._meta_insights("library")
        second = self._meta_insights("web application")

        with (
            patch("agents.meta_agent.get_repo_state_hash", return_value="NoRepoStateHash"),
            patch.object(agent, "analyze_project_metadata", side_effect=[first, second]) as analyze_mock,
        ):
            loaded_first = agent.get_meta_context()
            loaded_second = agent.get_meta_context()

        self.assertEqual(analyze_mock.call_count, 2)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())

    def test_get_meta_context_cache_init_failure_falls_back_to_recompute(self):
        mock_llm = self._mock_llm("meta-model-v1")
        mock_parsing_llm = self._mock_llm("parse-model-v1")
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        recomputed = self._meta_insights("library")
        with (
            patch("agents.meta_agent.get_repo_state_hash", return_value="state-a"),
            patch.object(agent, "_meta_cache_path", side_effect=PermissionError("read-only")),
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

        with patch("agents.meta_agent.get_repo_state_hash", return_value="state-a"):
            with patch.object(agent_a, "analyze_project_metadata", return_value=first) as analyze_a:
                loaded_first = agent_a.get_meta_context()
            with patch.object(agent_b, "analyze_project_metadata", return_value=second) as analyze_b:
                loaded_second = agent_b.get_meta_context()

        self.assertEqual(analyze_a.call_count, 1)
        self.assertEqual(analyze_b.call_count, 1)
        self.assertEqual(loaded_first.model_dump(), first.model_dump())
        self.assertEqual(loaded_second.model_dump(), second.model_dump())

    def test_get_meta_context_rejects_agent_llm_override(self):
        mock_llm = self._mock_llm("meta-model-v1")
        mock_parsing_llm = self._mock_llm("parse-model-v1")
        override_llm = self._mock_llm("meta-model-v2")
        agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        with self.assertRaises(ValueError):
            agent.get_meta_context(agent_llm=override_llm)


if __name__ == "__main__":
    unittest.main()
