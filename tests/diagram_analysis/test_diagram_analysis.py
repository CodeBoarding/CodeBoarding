import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    SourceCodeReference,
    UpdateAnalysis,
)
from diagram_analysis.analysis_json import (
    AnalysisInsightsJson,
    ComponentJson,
    from_analysis_to_json,
    from_component_to_json_component,
)
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.version import Version
from static_analyzer.analysis_result import StaticAnalysisResults


class TestVersion(unittest.TestCase):
    def test_version_creation(self):
        # Test creating a Version instance
        version = Version(commit_hash="abc123", code_boarding_version="1.0.0")

        self.assertEqual(version.commit_hash, "abc123")
        self.assertEqual(version.code_boarding_version, "1.0.0")

    def test_version_model_dump(self):
        # Test model serialization
        version = Version(commit_hash="def456", code_boarding_version="2.0.0")
        data = version.model_dump()

        self.assertEqual(data["commit_hash"], "def456")
        self.assertEqual(data["code_boarding_version"], "2.0.0")

    def test_version_model_dump_json(self):
        # Test JSON serialization
        version = Version(commit_hash="ghi789", code_boarding_version="3.0.0")
        json_str = version.model_dump_json()

        data = json.loads(json_str)
        self.assertEqual(data["commit_hash"], "ghi789")
        self.assertEqual(data["code_boarding_version"], "3.0.0")


class TestComponentJson(unittest.TestCase):
    def test_component_json_creation(self):
        # Test creating a ComponentJson instance
        comp = ComponentJson(
            name="TestComponent",
            description="Test description",
            can_expand=True,
            assigned_files=["file1.py", "file2.py"],
            key_entities=[],
        )

        self.assertEqual(comp.name, "TestComponent")
        self.assertEqual(comp.description, "Test description")
        self.assertTrue(comp.can_expand)
        self.assertEqual(comp.assigned_files, ["file1.py", "file2.py"])

    def test_component_json_defaults(self):
        # Test default values
        comp = ComponentJson(
            name="Component",
            description="Description",
            key_entities=[],
        )

        self.assertFalse(comp.can_expand)
        self.assertEqual(comp.assigned_files, [])

    def test_component_json_with_references(self):
        # Test with source code references
        ref = SourceCodeReference(
            qualified_name="test.TestClass", reference_file="test.py", reference_start_line=1, reference_end_line=10
        )
        comp = ComponentJson(name="Component", description="Description", key_entities=[ref])

        self.assertEqual(len(comp.key_entities), 1)
        self.assertEqual(comp.key_entities[0].qualified_name, "test.TestClass")


class TestAnalysisInsightsJson(unittest.TestCase):
    def test_analysis_insights_json_creation(self):
        # Test creating an AnalysisInsightsJson instance
        comp1 = ComponentJson(name="Comp1", description="Description 1", key_entities=[])
        comp2 = ComponentJson(name="Comp2", description="Description 2", key_entities=[])
        rel = Relation(src_name="Comp1", dst_name="Comp2", relation="uses")

        analysis = AnalysisInsightsJson(
            description="Test analysis",
            components=[comp1, comp2],
            components_relations=[rel],
        )

        self.assertEqual(analysis.description, "Test analysis")
        self.assertEqual(len(analysis.components), 2)
        self.assertEqual(len(analysis.components_relations), 1)

    def test_analysis_insights_json_model_dump(self):
        # Test serialization
        comp = ComponentJson(name="Comp", description="Description", key_entities=[])
        analysis = AnalysisInsightsJson(description="Test", components=[comp], components_relations=[])

        data = analysis.model_dump()
        self.assertEqual(data["description"], "Test")
        self.assertEqual(len(data["components"]), 1)


class TestAnalysisJsonConversion(unittest.TestCase):
    def setUp(self):
        # Create sample components
        self.comp1 = Component(
            name="Component1",
            description="First component",
            key_entities=[],
            assigned_files=["file1.py"],
        )
        self.comp2 = Component(
            name="Component2",
            description="Second component",
            key_entities=[],
            assigned_files=["file2.py"],
        )

        # Create sample relation
        self.rel = Relation(src_name="Component1", dst_name="Component2", relation="depends on")

        # Create sample analysis
        self.analysis = AnalysisInsights(
            description="Test application",
            components=[self.comp1, self.comp2],
            components_relations=[self.rel],
        )

    def test_from_component_to_json_component_can_expand_true(self):
        # Test when component can be expanded
        new_components = [self.comp1]  # comp1 can be expanded

        result = from_component_to_json_component(self.comp1, new_components)

        self.assertIsInstance(result, ComponentJson)
        self.assertEqual(result.name, "Component1")
        self.assertTrue(result.can_expand)

    def test_from_component_to_json_component_can_expand_false(self):
        # Test when component cannot be expanded
        new_components: list[Component] = []  # No new components

        result = from_component_to_json_component(self.comp1, new_components)

        self.assertIsInstance(result, ComponentJson)
        self.assertEqual(result.name, "Component1")
        self.assertFalse(result.can_expand)

    def test_from_component_to_json_component_preserves_data(self):
        # Test that all data is preserved
        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=5,
            reference_end_line=15,
        )
        comp = Component(
            name="TestComp",
            description="Test description",
            assigned_files=["a.py", "b.py"],
            key_entities=[ref],
        )

        result = from_component_to_json_component(comp, [])

        self.assertEqual(result.name, "TestComp")
        self.assertEqual(result.description, "Test description")
        self.assertEqual(set(result.assigned_files), {"a.py", "b.py"})
        self.assertEqual(len(result.key_entities), 1)

    def test_from_analysis_to_json(self):
        # Test full analysis conversion to JSON
        new_components = [self.comp1]  # Only comp1 can expand

        json_str = from_analysis_to_json(self.analysis, new_components)

        # Parse JSON to verify it's valid
        data = json.loads(json_str)

        self.assertEqual(data["description"], "Test application")
        self.assertEqual(len(data["components"]), 2)
        self.assertEqual(len(data["components_relations"]), 1)

        # Verify can_expand flags
        comp1_data = next(c for c in data["components"] if c["name"] == "Component1")
        comp2_data = next(c for c in data["components"] if c["name"] == "Component2")

        self.assertTrue(comp1_data["can_expand"])
        self.assertFalse(comp2_data["can_expand"])

    def test_from_analysis_to_json_empty(self):
        # Test with empty analysis
        empty_analysis = AnalysisInsights(description="Empty", components=[], components_relations=[])

        json_str = from_analysis_to_json(empty_analysis, [])

        data = json.loads(json_str)
        self.assertEqual(data["description"], "Empty")
        self.assertEqual(len(data["components"]), 0)
        self.assertEqual(len(data["components_relations"]), 0)

    def test_from_analysis_to_json_with_references(self):
        # Test with source code references
        ref1 = SourceCodeReference(
            qualified_name="src.class1.Class1",
            reference_file="src/class1.py",
            reference_start_line=10,
            reference_end_line=20,
        )
        ref2 = SourceCodeReference(
            qualified_name="src.class2.Class2",
            reference_file="src/class2.py",
            reference_start_line=5,
            reference_end_line=15,
        )

        comp = Component(
            name="WithRefs",
            description="Component with references",
            assigned_files=[],
            key_entities=[ref1, ref2],
        )

        analysis = AnalysisInsights(description="Test", components=[comp], components_relations=[])

        json_str = from_analysis_to_json(analysis, [])
        data = json.loads(json_str)

        comp_data = data["components"][0]
        self.assertEqual(len(comp_data["key_entities"]), 2)

    def test_from_analysis_to_json_formatting(self):
        # Test that JSON is properly formatted with indentation
        json_str = from_analysis_to_json(self.analysis, [])

        # Check that it's indented (contains newlines and spaces)
        self.assertIn("\n", json_str)
        self.assertIn("  ", json_str)  # 2-space indentation


class TestDiagramGenerator(unittest.TestCase):
    def setUp(self):
        # Create temporary directories for testing
        self.temp_dir = tempfile.mkdtemp()
        self.repo_location = Path(self.temp_dir) / "test_repo"
        self.repo_location.mkdir(parents=True)
        self.temp_folder = Path(self.temp_dir) / "temp"
        self.temp_folder.mkdir(parents=True)
        self.output_dir = Path(self.temp_dir) / "output"
        self.output_dir.mkdir(parents=True)

        # Create a simple test file
        (self.repo_location / "test.py").write_text("def test(): pass")

    def tearDown(self):
        # Clean up temporary directory
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init(self):
        # Test DiagramGenerator initialization
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
        )

        self.assertEqual(gen.repo_location, self.repo_location)
        self.assertEqual(gen.repo_name, "test_repo")
        self.assertEqual(gen.output_dir, self.output_dir)
        self.assertEqual(gen.depth_level, 2)
        self.assertIsNone(gen.details_agent)
        self.assertIsNone(gen.abstraction_agent)

    @patch("diagram_analysis.diagram_generator.ProjectScanner")
    @patch("diagram_analysis.diagram_generator.StaticAnalyzer")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    @patch("diagram_analysis.diagram_generator.PlannerAgent")
    @patch("diagram_analysis.diagram_generator.DiffAnalyzingAgent")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash")
    def test_pre_analysis(
        self,
        mock_git_hash,
        mock_diff_agent,
        mock_planner,
        mock_abstraction,
        mock_details,
        mock_meta,
        mock_static,
        mock_scanner,
    ):
        # Test pre_analysis method
        mock_git_hash.return_value = "abc123"
        mock_static_instance = Mock()
        # Return a proper StaticAnalysisResults object instead of dict
        mock_analysis_results = StaticAnalysisResults()
        mock_static_instance.analyze.return_value = mock_analysis_results
        mock_static.return_value = mock_static_instance

        mock_meta_instance = Mock()
        mock_meta_instance.analyze_project_metadata.return_value = {"meta": "context"}
        mock_meta.return_value = mock_meta_instance

        # Mock ProjectScanner to avoid tokei dependency
        mock_scanner_instance = Mock()
        mock_scanner_instance.scan.return_value = []
        mock_scanner.return_value = mock_scanner_instance

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
        )

        gen.pre_analysis()

        # Verify agents were created
        self.assertIsNotNone(gen.meta_agent)
        self.assertIsNotNone(gen.details_agent)
        self.assertIsNotNone(gen.abstraction_agent)
        self.assertIsNotNone(gen.planner_agent)
        self.assertIsNotNone(gen.diff_analyzer_agent)

        # Verify version file was created
        version_file = self.output_dir / "codeboarding_version.json"
        self.assertTrue(version_file.exists())

    @patch("diagram_analysis.diagram_generator.StaticAnalyzer")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    @patch("diagram_analysis.diagram_generator.PlannerAgent")
    @patch("diagram_analysis.diagram_generator.DiffAnalyzingAgent")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash")
    def test_process_component_no_update_needed(
        self,
        mock_git_hash,
        mock_diff_agent_class,
        mock_planner_class,
        mock_abstraction,
        mock_details_class,
        mock_meta,
        mock_static,
    ):
        # Test processing a component that doesn't need update
        mock_git_hash.return_value = "abc123"

        # Setup mocks
        mock_static_instance = Mock()
        mock_static_instance.analyze.return_value = {"test": "data"}
        mock_static.return_value = mock_static_instance

        mock_meta_instance = Mock()
        mock_meta_instance.analyze_project_metadata.return_value = {"meta": "context"}
        mock_meta.return_value = mock_meta_instance

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
        )

        # Setup agents
        gen.diff_analyzer_agent = Mock()
        gen.details_agent = Mock()
        gen.planner_agent = Mock()

        # Mock update analysis - no update needed (degree < 4)
        update_analysis = Mock()
        update_analysis.update_degree = 2
        gen.diff_analyzer_agent.check_for_component_updates.return_value = update_analysis

        # Mock existing analysis
        existing_analysis = AnalysisInsights(description="Test component", components=[], components_relations=[])
        gen.diff_analyzer_agent.get_component_analysis.return_value = existing_analysis

        # Create test component
        component = Component(name="TestComponent", description="Test", key_entities=[])

        result_path, new_components = gen.process_component(component)

        # Should return path and no new components since update not needed
        self.assertIsNotNone(result_path)
        self.assertEqual(new_components, [])

    @patch("diagram_analysis.diagram_generator.StaticAnalyzer")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    @patch("diagram_analysis.diagram_generator.PlannerAgent")
    @patch("diagram_analysis.diagram_generator.DiffAnalyzingAgent")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash")
    def test_process_component_partial_update(
        self,
        mock_git_hash,
        mock_diff_agent_class,
        mock_planner_class,
        mock_abstraction,
        mock_details_class,
        mock_meta,
        mock_static,
    ):
        # Test processing a component that needs partial update
        mock_git_hash.return_value = "abc123"

        # Setup mocks
        mock_static_instance = Mock()
        mock_static_instance.analyze.return_value = {"test": "data"}
        mock_static.return_value = mock_static_instance

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
        )

        # Setup agents
        gen.diff_analyzer_agent = Mock()
        gen.details_agent = Mock()
        gen.planner_agent = Mock()

        # Mock update analysis - partial update needed (4 < degree < 8)
        update_analysis = Mock()
        update_analysis.update_degree = 6
        update_analysis.feedback = "Needs minor updates"
        gen.diff_analyzer_agent.check_for_component_updates.return_value = update_analysis

        # Mock existing analysis
        existing_analysis = AnalysisInsights(description="Test component", components=[], components_relations=[])
        gen.diff_analyzer_agent.get_component_analysis.return_value = existing_analysis

        # Mock apply_feedback
        updated_analysis = AnalysisInsights(description="Updated component", components=[], components_relations=[])
        gen.details_agent.apply_feedback.return_value = updated_analysis
        gen.details_agent.classify_files.return_value = None

        # Mock planner
        gen.planner_agent.plan_analysis.return_value = []

        # Create test component
        component = Component(name="TestComponent", description="Test", key_entities=[])

        result_path, new_components = gen.process_component(component)

        # Should apply feedback and return result
        self.assertIsNotNone(result_path)
        gen.details_agent.apply_feedback.assert_called_once()

    @patch("diagram_analysis.diagram_generator.StaticAnalyzer")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    @patch("diagram_analysis.diagram_generator.PlannerAgent")
    @patch("diagram_analysis.diagram_generator.DiffAnalyzingAgent")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash")
    def test_process_component_full_update(
        self,
        mock_git_hash,
        mock_diff_agent_class,
        mock_planner_class,
        mock_abstraction,
        mock_details_class,
        mock_meta,
        mock_static,
    ):
        # Test processing a component that needs full update
        mock_git_hash.return_value = "abc123"

        # Setup mocks
        mock_static_instance = Mock()
        mock_static_instance.analyze.return_value = {"test": "data"}
        mock_static.return_value = mock_static_instance

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
        )

        # Setup agents
        gen.diff_analyzer_agent = Mock()
        gen.details_agent = Mock()
        gen.planner_agent = Mock()

        # Mock update analysis - full update needed (degree >= 8)
        update_analysis = Mock()
        update_analysis.update_degree = 10
        gen.diff_analyzer_agent.check_for_component_updates.return_value = update_analysis

        # Mock analysis
        new_analysis = AnalysisInsights(description="New component analysis", components=[], components_relations=[])
        gen.details_agent.run.return_value = (new_analysis, {})
        gen.details_agent.classify_files.return_value = None

        # Mock planner
        gen.planner_agent.plan_analysis.return_value = []

        # Create test component
        component = Component(name="TestComponent", description="Test", key_entities=[])

        result_path, new_components = gen.process_component(component)

        # Should run full analysis
        self.assertIsNotNone(result_path)
        gen.details_agent.run.assert_called_once_with(component)

    @patch("diagram_analysis.diagram_generator.StaticAnalyzer")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    @patch("diagram_analysis.diagram_generator.PlannerAgent")
    @patch("diagram_analysis.diagram_generator.DiffAnalyzingAgent")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash")
    def test_process_component_with_invalid_feedback(
        self,
        mock_git_hash,
        mock_diff_agent_class,
        mock_planner_class,
        mock_abstraction,
        mock_details_class,
        mock_meta,
        mock_static,
    ):
        # Test processing a component with invalid feedback that needs correction
        mock_git_hash.return_value = "abc123"

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
        )

        # Setup agents
        gen.diff_analyzer_agent = Mock()
        gen.details_agent = Mock()
        gen.planner_agent = Mock()

        # Mock update analysis - partial update needed (4 < degree < 8) to trigger apply_feedback
        update_analysis = Mock()
        update_analysis.update_degree = 6
        update_analysis.feedback = "Needs correction"
        gen.diff_analyzer_agent.check_for_component_updates.return_value = update_analysis

        # Mock existing analysis for partial update path
        existing_analysis = AnalysisInsights(description="Existing analysis", components=[], components_relations=[])
        gen.diff_analyzer_agent.get_component_analysis.return_value = existing_analysis

        # Mock apply_feedback
        corrected_analysis = AnalysisInsights(description="Corrected analysis", components=[], components_relations=[])
        gen.details_agent.apply_feedback.return_value = corrected_analysis
        gen.details_agent.classify_files.return_value = None

        # Mock planner
        gen.planner_agent.plan_analysis.return_value = []

        # Create test component
        component = Component(name="TestComponent", description="Test", key_entities=[])

        result_path, new_components = gen.process_component(component)

        # Should apply feedback after invalid validation
        self.assertIsNotNone(result_path)
        gen.details_agent.apply_feedback.assert_called_once()

    @patch("diagram_analysis.diagram_generator.StaticAnalyzer")
    def test_process_component_with_exception(self, mock_static):
        # Test processing a component that raises an exception
        mock_static_instance = Mock()
        mock_static_instance.analyze.return_value = {"test": "data"}
        mock_static.return_value = mock_static_instance

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
        )

        # Setup agents
        gen.diff_analyzer_agent = Mock()
        gen.details_agent = Mock()
        gen.planner_agent = Mock()

        # Mock to raise exception
        gen.diff_analyzer_agent.check_for_component_updates.side_effect = Exception("Test error")

        # Create test component
        component = Component(name="TestComponent", description="Test", key_entities=[])

        result_path, new_components = gen.process_component(component)

        # Should return None and empty list on exception
        self.assertIsNone(result_path)
        self.assertEqual(new_components, [])

    @patch("diagram_analysis.diagram_generator.StaticAnalyzer")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    @patch("diagram_analysis.diagram_generator.PlannerAgent")
    @patch("diagram_analysis.diagram_generator.DiffAnalyzingAgent")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash")
    def test_generate_analysis_no_updates_needed(
        self,
        mock_git_hash,
        mock_diff_agent_class,
        mock_planner_class,
        mock_abstraction_class,
        mock_details_class,
        mock_meta_class,
        mock_static,
    ):
        # Test generate_analysis when no updates are needed
        mock_git_hash.return_value = "abc123"

        # Setup mocks
        mock_static_instance = Mock()
        mock_static_instance.analyze.return_value = {"test": "data"}
        mock_static.return_value = mock_static_instance

        mock_meta_instance = Mock()
        mock_meta_instance.analyze_project_metadata.return_value = {"meta": "context"}
        mock_meta_class.return_value = mock_meta_instance

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=1,  # Only process one level
        )

        # Setup agents
        gen.details_agent = Mock()
        gen.diff_analyzer_agent = Mock()
        gen.abstraction_agent = Mock()
        gen.planner_agent = Mock()

        # Mock update check - no update needed
        update_analysis = UpdateAnalysis(update_degree=2, feedback="")
        gen.diff_analyzer_agent.check_for_updates.return_value = update_analysis

        # Mock existing analysis
        existing_analysis = AnalysisInsights(description="Existing analysis", components=[], components_relations=[])
        gen.diff_analyzer_agent.get_analysis.return_value = existing_analysis
        gen.abstraction_agent.classify_files.return_value = None

        # Mock planner
        gen.planner_agent.plan_analysis.return_value = []

        files = gen.generate_analysis()

        # Should generate at least the root analysis file
        self.assertGreater(len(files), 0)


if __name__ == "__main__":
    unittest.main()
