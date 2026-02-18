import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    @patch("agents.meta_agent.create_react_agent")
    @patch("agents.agent.create_react_agent")
    def test_meta_agent_uses_external_deps_tool(self, mock_base_create_react_agent, mock_meta_create_react_agent):
        # MetaAgent should use readExternalDeps and not rely on getPackageDependencies
        mock_base_create_react_agent.return_value = MagicMock()
        mock_meta_create_react_agent.return_value = MagicMock()

        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.project_name,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        call_args = mock_meta_create_react_agent.call_args
        self.assertIsNotNone(call_args)
        tools = call_args.kwargs["tools"]
        tool_names = {tool.name for tool in tools}

        self.assertIn("readDocs", tool_names)
        self.assertIn("readExternalDeps", tool_names)
        self.assertNotIn("getPackageDependencies", tool_names)

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


if __name__ == "__main__":
    unittest.main()
