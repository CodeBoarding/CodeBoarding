from typing import cast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, PropertyMock

from agents.details_agent import DetailsAgent
from agents.agent_responses import (
    AnalysisInsights,
    CFGAnalysisInsights,
    Component,
    MetaAnalysisInsights,
    SourceCodeReference,
    ValidationInsights,
)
from static_analyzer.analysis_result import StaticAnalysisResults


class TestDetailsAgent(unittest.TestCase):
    def setUp(self):
        # Create mock static analysis
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]

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

        # Create test component
        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        self.test_component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
            assigned_files=["test.py", "test_utils.py"],
        )

    def tearDown(self):
        import shutil

        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    def test_init(self, mock_create_llm, mock_create_instructor):
        # Test initialization
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("subcfg", agent.prompts)
        self.assertIn("cfg", agent.prompts)
        self.assertIn("structure", agent.prompts)
        self.assertIn("final_analysis", agent.prompts)
        self.assertIn("feedback", agent.prompts)
        self.assertIn("classification", agent.prompts)

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    def test_step_subcfg(self, mock_create_llm, mock_create_instructor):
        # Test step_subcfg
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        # Mock the read_cfg_tool property
        mock_tool = MagicMock()
        mock_tool.component_cfg.return_value = "Mock CFG data"
        with patch.object(DetailsAgent, "read_cfg_tool", new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = mock_tool

            agent.step_subcfg(self.test_component)

            self.assertIn("subcfg_insight", agent.context)
            mock_tool.component_cfg.assert_called_once_with(self.test_component)

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    def test_step_cfg(self, mock_parse_invoke, mock_create_llm, mock_create_instructor):
        # Test step_cfg
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        # Set up context
        mock_insight = cast(AnalysisInsights, Mock())
        agent.context["subcfg_insight"] = mock_insight

        mock_response = CFGAnalysisInsights(
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.step_cfg(self.test_component)

        self.assertEqual(result, mock_response)
        self.assertIn("cfg_insight", agent.context)
        mock_parse_invoke.assert_called_once()

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    def test_step_enhance_structure(self, mock_parse_invoke, mock_create_llm, mock_create_instructor):
        # Test step_enhance_structure
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        # Set up context
        cfg_insight = CFGAnalysisInsights(
            components=[],
            components_relations=[],
        )
        agent.context["cfg_insight"] = cfg_insight

        mock_response = AnalysisInsights(
            description="Structure analysis",
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.step_enhance_structure(self.test_component)

        self.assertEqual(result, mock_response)
        self.assertIn("structure_insight", agent.context)
        mock_parse_invoke.assert_called_once()

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    def test_step_analysis(self, mock_parse_invoke, mock_create_llm, mock_create_instructor):
        # Test step_analysis
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        # Set up context
        structure_insight = AnalysisInsights(
            description="Structure insight",
            components=[],
            components_relations=[],
        )
        agent.context["structure_insight"] = structure_insight

        mock_response = AnalysisInsights(
            description="Final analysis",
            components=[],
            components_relations=[],
        )
        mock_parse_invoke.return_value = mock_response

        result = agent.step_analysis(self.test_component)

        self.assertEqual(result, mock_response)
        mock_parse_invoke.assert_called_once()

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    @patch("agents.details_agent.DetailsAgent.fix_source_code_reference_lines")
    def test_apply_feedback(self, mock_fix_ref, mock_parse_invoke, mock_create_llm, mock_create_instructor):
        # Test apply_feedback
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
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
            additional_info="Please improve",
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

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    @patch("agents.details_agent.DetailsAgent.fix_source_code_reference_lines")
    def test_run(self, mock_fix_ref, mock_parse_invoke, mock_create_llm, mock_create_instructor):
        # Test run method
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        # Mock the read_cfg_tool
        mock_tool = MagicMock()
        mock_tool.component_cfg.return_value = "Mock CFG data"
        with patch.object(DetailsAgent, "read_cfg_tool", new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = mock_tool

            # Mock responses for each step
            cfg_response = CFGAnalysisInsights(
                components=[],
                components_relations=[],
            )
            structure_response = AnalysisInsights(
                description="Structure",
                components=[],
                components_relations=[],
            )
            final_response = AnalysisInsights(
                description="Final",
                components=[],
                components_relations=[],
            )

            mock_parse_invoke.side_effect = [cfg_response, structure_response, final_response]
            mock_fix_ref.return_value = final_response

            result = agent.run(self.test_component)

            self.assertEqual(result, final_response)
            self.assertEqual(mock_parse_invoke.call_count, 3)
            mock_fix_ref.assert_called_once()

    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_llm_from_env")
    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    @patch("os.path.exists")
    @patch("os.path.relpath")
    def test_classify_files(
        self, mock_relpath, mock_exists, mock_parse_invoke, mock_create_llm, mock_create_instructor
    ):
        # Test classify_files
        mock_create_llm.return_value = (MagicMock(), "test-model", MagicMock())
        mock_create_instructor.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        from agents.agent_responses import ComponentFiles, FileClassification

        sub_component = Component(
            name="SubComponent",
            description="Sub component",
            key_entities=[],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[sub_component],
            components_relations=[],
        )

        # Mock file classification response
        mock_classification = ComponentFiles(
            file_paths=[
                FileClassification(file_path="test.py", component_name="SubComponent"),
                FileClassification(file_path="test_utils.py", component_name="Unclassified"),
            ]
        )
        mock_parse_invoke.return_value = mock_classification
        mock_exists.return_value = True
        mock_relpath.side_effect = lambda path, start: path

        agent.classify_files(self.test_component, analysis)

        # Check that Unclassified component was added
        self.assertTrue(any(c.name == "Unclassified" for c in analysis.components))


if __name__ == "__main__":
    unittest.main()
