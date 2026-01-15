import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.planner_agent import PlannerAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    ExpandComponent,
    SourceCodeReference,
)
from static_analyzer.analysis_result import StaticAnalysisResults


class TestPlannerAgent(unittest.TestCase):
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
        agent = PlannerAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        self.assertIsNotNone(agent.expansion_prompt)
        self.assertIsNotNone(agent.agent)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.planner_agent.PlannerAgent._parse_invoke")
    def test_plan_analysis_all_expandable(self, mock_parse_invoke, mock_static_init):
        # Test plan_analysis where all components should expand
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = PlannerAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref1 = SourceCodeReference(
            qualified_name="comp1.Class1",
            reference_file="comp1.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        ref2 = SourceCodeReference(
            qualified_name="comp2.Class2",
            reference_file="comp2.py",
            reference_start_line=1,
            reference_end_line=20,
        )

        component1 = Component(
            name="Component1",
            description="First component",
            key_entities=[ref1],
        )

        component2 = Component(
            name="Component2",
            description="Second component",
            key_entities=[ref2],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component1, component2],
            components_relations=[],
        )

        # Mock all components as expandable
        mock_parse_invoke.return_value = ExpandComponent(
            should_expand=True, reason="Component is complex and requires expansion"
        )

        result = agent.plan_analysis(analysis)

        self.assertEqual(len(result), 2)
        self.assertIn(component1, result)
        self.assertIn(component2, result)
        self.assertEqual(mock_parse_invoke.call_count, 2)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.planner_agent.PlannerAgent._parse_invoke")
    def test_plan_analysis_some_expandable(self, mock_parse_invoke, mock_static_init):
        # Test plan_analysis where only some components should expand
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = PlannerAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref1 = SourceCodeReference(
            qualified_name="comp1.Class1",
            reference_file="comp1.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        ref2 = SourceCodeReference(
            qualified_name="comp2.Class2",
            reference_file="comp2.py",
            reference_start_line=1,
            reference_end_line=20,
        )

        component1 = Component(
            name="Component1",
            description="Expandable component",
            key_entities=[ref1],
        )

        component2 = Component(
            name="Component2",
            description="Non-expandable component",
            key_entities=[ref2],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component1, component2],
            components_relations=[],
        )

        # Mock: first component expandable, second not
        mock_parse_invoke.side_effect = [
            ExpandComponent(should_expand=True, reason="Component is complex"),
            ExpandComponent(should_expand=False, reason="Component is simple"),
        ]

        result = agent.plan_analysis(analysis)

        self.assertEqual(len(result), 1)
        self.assertIn(component1, result)
        self.assertNotIn(component2, result)
        self.assertEqual(mock_parse_invoke.call_count, 2)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.planner_agent.PlannerAgent._parse_invoke")
    def test_plan_analysis_none_expandable(self, mock_parse_invoke, mock_static_init):
        # Test plan_analysis where no components should expand
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = PlannerAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        ref = SourceCodeReference(
            qualified_name="comp.Class",
            reference_file="comp.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        component = Component(
            name="Component",
            description="Simple component",
            key_entities=[ref],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        # Mock component as not expandable
        mock_parse_invoke.return_value = ExpandComponent(
            should_expand=False, reason="Component is too simple to expand"
        )

        result = agent.plan_analysis(analysis)

        self.assertEqual(len(result), 0)
        mock_parse_invoke.assert_called_once()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.planner_agent.PlannerAgent._parse_invoke")
    def test_plan_analysis_empty_components(self, mock_parse_invoke, mock_static_init):
        # Test plan_analysis with no components
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = PlannerAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
        )

        analysis = AnalysisInsights(
            description="Empty analysis",
            components=[],
            components_relations=[],
        )

        result = agent.plan_analysis(analysis)

        self.assertEqual(len(result), 0)
        mock_parse_invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
