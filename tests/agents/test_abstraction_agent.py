import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import (
    AnalysisInsights,
    CFGAnalysisInsights,
    Component,
    MetaAnalysisInsights,
    SourceCodeReference,
    ValidationInsights,
)
from static_analyzer.analysis_result import StaticAnalysisResults


class TestAbstractionAgent(unittest.TestCase):
    def setUp(self):
        # Create mock static analysis
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_all_source_files.return_value = [
            Path("test_file.py"),
            Path("another_file.py"),
        ]

        # Create mock CFG
        mock_cfg = MagicMock()
        mock_cfg.to_cluster_string.return_value = "Mock CFG string"
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        # Create mock meta context
        self.mock_meta_context = MetaAnalysisInsights(
            project_type="library",
            domain="software development",
            architectural_patterns=["layered architecture"],
            expected_components=["core", "utils"],
            technology_stack=["Python"],
            architectural_bias="Focus on modularity",
        )

        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.project_name = "test_project"

    def tearDown(self):
        import shutil

        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_init(self, mock_static_init):
        # Test initialization
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("cfg", agent.prompts)
        self.assertIn("source", agent.prompts)
        self.assertIn("final_analysis", agent.prompts)
        self.assertIn("classification", agent.prompts)
        self.assertIn("feedback", agent.prompts)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._parse_invoke")
    def test_step_cfg_single_language(self, mock_parse_invoke, mock_static_init):
        # Test step_cfg with single language
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        mock_response = CFGAnalysisInsights(
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.step_cfg()

        self.assertEqual(result, mock_response)
        self.assertIn("cfg_insight", agent.context)
        mock_parse_invoke.assert_called_once()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._parse_invoke")
    def test_step_cfg_multiple_languages(self, mock_parse_invoke, mock_static_init):
        # Test step_cfg with multiple languages
        mock_static_init.return_value = (MagicMock(), "test-model")
        self.mock_static_analysis.get_languages.return_value = ["python", "javascript"]

        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        mock_response = CFGAnalysisInsights(
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.step_cfg()

        self.assertEqual(result, mock_response)
        self.mock_static_analysis.get_cfg.assert_called()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._parse_invoke")
    def test_step_cfg_no_languages(self, mock_parse_invoke, mock_static_init):
        # Test step_cfg with no languages detected
        mock_static_init.return_value = (MagicMock(), "test-model")
        self.mock_static_analysis.get_languages.return_value = []

        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        mock_response = CFGAnalysisInsights(
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.step_cfg()

        self.assertEqual(result, mock_response)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._parse_invoke")
    def test_step_source(self, mock_parse_invoke, mock_static_init):
        # Test step_source
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        # Add some context
        cfg_insight = CFGAnalysisInsights(
            components=[],
            components_relations=[],
        )
        agent.context["cfg_insight"] = cfg_insight

        mock_response = AnalysisInsights(
            description="Test source analysis",
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.step_source()

        self.assertEqual(result, mock_response)
        self.assertIn("source", agent.context)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._parse_invoke")
    def test_generate_analysis(self, mock_parse_invoke, mock_static_init):
        # Test generate_analysis
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        # Add context
        cfg_insight = AnalysisInsights(
            description="CFG insight",
            components=[],
            components_relations=[],
        )
        source_insight = AnalysisInsights(
            description="Source insight",
            components=[],
            components_relations=[],
        )
        agent.context["cfg_insight"] = cfg_insight
        agent.context["source"] = source_insight

        mock_response = AnalysisInsights(
            description="Final analysis",
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.generate_analysis()

        self.assertEqual(result, mock_response)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._parse_invoke")
    @patch("agents.abstraction_agent.AbstractionAgent.fix_source_code_reference_lines")
    def test_apply_feedback(self, mock_fix_ref, mock_parse_invoke, mock_static_init):
        # Test apply_feedback
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        analysis = AnalysisInsights(
            description="Original analysis",
            components=[],
            components_relations=[],
        )

        feedback = ValidationInsights(
            additional_info="Please improve the analysis",
            is_valid=False,
        )

        updated_analysis = AnalysisInsights(
            description="Updated analysis",
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = updated_analysis
        mock_fix_ref.return_value = updated_analysis

        result = agent.apply_feedback(analysis, feedback)

        mock_parse_invoke.assert_called_once()
        mock_fix_ref.assert_called_once_with(updated_analysis)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._parse_invoke")
    @patch("os.path.exists")
    @patch("os.path.relpath")
    def test_classify_files(self, mock_relpath, mock_exists, mock_parse_invoke, mock_static_init):
        # Test classify_files
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        from agents.agent_responses import ComponentFiles, FileClassification

        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            referenced_source_code=[ref],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        # Mock file classification response
        mock_classification = ComponentFiles(
            file_paths=[
                FileClassification(file_path="test_file.py", component_name="TestComponent"),
                FileClassification(file_path="another_file.py", component_name="Unclassified"),
            ]
        )
        mock_parse_invoke.return_value = mock_classification
        mock_exists.return_value = True
        mock_relpath.side_effect = lambda path, start: path

        agent.classify_files(analysis)

        # Check that Unclassified component was added
        self.assertTrue(any(c.name == "Unclassified" for c in analysis.components))

        # Check that files were assigned
        self.assertTrue(len(component.assigned_files) > 0)


if __name__ == "__main__":
    unittest.main()
