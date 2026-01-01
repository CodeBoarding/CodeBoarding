import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.validator_agent import ValidatorAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    SourceCodeReference,
    ValidationInsights,
)
from static_analyzer.analysis_result import StaticAnalysisResults


class TestValidatorAgent(unittest.TestCase):
    def setUp(self):
        # Create mock static analysis
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]

        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil

        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_init(self, mock_static_init):
        # Test initialization
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        self.assertIsNotNone(agent.valid_component_prompt)
        self.assertIsNotNone(agent.valid_relations_prompt)
        self.assertIsNotNone(agent.agent)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.validator_agent.ValidatorAgent._parse_invoke")
    def test_validate_components(self, mock_parse_invoke, mock_static_init):
        # Test validate_components
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        mock_validation = ValidationInsights(
            additional_info="Components are valid",
            is_valid=True,
        )
        mock_parse_invoke.return_value = mock_validation

        result = agent.validate_components(analysis)

        self.assertEqual(result, mock_validation)
        mock_parse_invoke.assert_called_once()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.validator_agent.ValidatorAgent._parse_invoke")
    def test_validate_relations(self, mock_parse_invoke, mock_static_init):
        # Test validate_relations
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
        )

        relation = Relation(
            relation="uses",
            src_name="ComponentA",
            dst_name="ComponentB",
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[relation],
        )

        mock_validation = ValidationInsights(
            additional_info="Relations are valid",
            is_valid=True,
        )
        mock_parse_invoke.return_value = mock_validation

        result = agent.validate_relations(analysis)

        self.assertEqual(result, mock_validation)
        mock_parse_invoke.assert_called_once()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_validate_references_no_references(self, mock_static_init):
        # Test validate_references with component having no references
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        result = agent.validate_references(analysis)

        self.assertIsInstance(result, ValidationInsights)
        self.assertFalse(result.is_valid)
        self.assertIn("no source code references", result.additional_info)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_validate_references_no_file(self, mock_static_init):
        # Test validate_references with reference having no file
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file=None,
            reference_start_line=None,
            reference_end_line=None,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        result = agent.validate_references(analysis)

        self.assertIsInstance(result, ValidationInsights)
        self.assertFalse(result.is_valid)
        self.assertIn("incorrect source references", result.additional_info)

    @patch("os.path.exists")
    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_validate_references_valid_reference(self, mock_static_init, mock_exists):
        # Test validate_references with valid reference
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        # Mock get_reference to return a node
        mock_node = MagicMock()
        mock_node.file_path = "test.py"
        mock_node.line_start = 1
        mock_node.line_end = 10
        mock_node.qualified_name = "test.TestClass"
        mock_exists.return_value = True
        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_reference.return_value = mock_node
        self.mock_static_analysis.get_reference.side_effect = None  # <--- Crucial to ensure no lingering side effect

        result = agent.validate_references(analysis)

        self.assertIsInstance(result, ValidationInsights)
        self.assertTrue(result.is_valid)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_validate_references_incorrect_file_path(self, mock_static_init):
        # Test validate_references with incorrect file path
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="wrong.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        # Mock get_reference to return a node with different file
        mock_node = MagicMock()
        mock_node.file_path = "test.py"
        mock_node.line_start = 1
        mock_node.line_end = 10
        self.mock_static_analysis.get_reference.return_value = mock_node

        result = agent.validate_references(analysis)

        self.assertIsInstance(result, ValidationInsights)
        self.assertFalse(result.is_valid)
        self.assertIn("incorrect source references", result.additional_info)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("os.path.exists")
    def test_validate_references_file_exists(self, mock_exists, mock_static_init):
        # Test validate_references with file reference that exists
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = ValidatorAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        expected_abs_path = os.path.join(str(self.repo_dir), "test", "module.py")

        ref = SourceCodeReference(
            qualified_name="test.module",
            reference_file=expected_abs_path,
            reference_start_line=None,
            reference_end_line=None,
        )

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_reference.side_effect = ValueError("Not found")

        # Mock exists to only be True for the one absolute path
        mock_exists.return_value = False
        mock_exists.side_effect = lambda path: str(path) == expected_abs_path or str(path) == ref.reference_file

        result = agent.validate_references(analysis)

        self.assertIsInstance(result, ValidationInsights)
        # Should be valid because file exists
        self.assertTrue(result.is_valid)


if __name__ == "__main__":
    unittest.main()
