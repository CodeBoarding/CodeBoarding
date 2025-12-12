import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    SourceCodeReference,
)
from output_generators.sphinx import (
    component_header,
    generate_rst,
    generate_rst_file,
    generated_mermaid_str,
)


class TestSphinxOutput(unittest.TestCase):
    def setUp(self):
        # Create test data
        self.ref1 = SourceCodeReference(
            qualified_name="test.Component1.method",
            reference_file="/repo/test_project/component1.py",
            reference_start_line=10,
            reference_end_line=20,
        )

        self.ref2 = SourceCodeReference(
            qualified_name="test.Component2.function",
            reference_file="/repo/test_project/component2.py",
            reference_start_line=5,
            reference_end_line=15,
        )

        self.component1 = Component(
            name="Component1",
            description="First test component",
            referenced_source_code=[self.ref1],
        )

        self.component2 = Component(
            name="Component2",
            description="Second test component",
            referenced_source_code=[self.ref2],
        )

        self.relation = Relation(
            relation="depends on",
            src_name="Component1",
            dst_name="Component2",
        )

        self.analysis = AnalysisInsights(
            description="Test project analysis",
            components=[self.component1, self.component2],
            components_relations=[self.relation],
        )

        self.linked_files = [Path("Component1.json"), Path("Component2.json")]
        self.repo_ref = "https://github.com/user/repo/blob/main"
        self.project = "test_project"

    def test_generated_mermaid_str(self):
        # Test Mermaid diagram generation in RST format
        result = generated_mermaid_str(
            self.analysis,
            linked_files=self.linked_files,
            repo_ref=self.repo_ref,
            project=self.project,
            demo=False,
        )

        self.assertIn(".. mermaid::", result)
        self.assertIn("graph LR", result)
        self.assertIn("Component1", result)
        self.assertIn("Component2", result)
        self.assertIn("depends on", result)

    def test_generated_mermaid_str_with_links(self):
        # Test Mermaid diagram with links in RST
        result = generated_mermaid_str(
            self.analysis,
            linked_files=self.linked_files,
            repo_ref=self.repo_ref,
            project=self.project,
            demo=False,
        )

        self.assertIn("click", result)
        self.assertIn(".html", result)

    def test_generated_mermaid_str_demo_mode(self):
        # Test Mermaid diagram in demo mode
        result = generated_mermaid_str(
            self.analysis,
            linked_files=self.linked_files,
            repo_ref=self.repo_ref,
            project=self.project,
            demo=True,
        )

        self.assertIn("click", result)
        self.assertIn("https://github.com/CodeBoarding/GeneratedOnBoardings", result)

    def test_component_header_with_link(self):
        # Test component header with link
        result = component_header("Component1", self.linked_files)

        self.assertIn("Component1", result)
        self.assertIn("^", result)  # RST underline character
        self.assertIn(":ref:`Expand", result)

    def test_component_header_without_link(self):
        # Test component header without link
        result = component_header("UnlinkedComponent", [])

        self.assertIn("UnlinkedComponent", result)
        self.assertIn("^", result)
        self.assertNotIn(":ref:", result)

    def test_component_header_length(self):
        # Test that header underline matches component name length
        component_name = "Test Component"
        result = component_header(component_name, [])

        lines = result.split("\n")
        self.assertEqual(len(lines[0]), len(lines[1]))

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst(self):
        # Test full RST generation
        result = generate_rst(
            self.analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=self.linked_files,
            demo=False,
            file_name="on_boarding",
        )

        self.assertIn("On Boarding", result)  # Title
        self.assertIn("=", result)  # Title underline
        self.assertIn(".. mermaid::", result)
        self.assertIn("Component1", result)
        self.assertIn("Component2", result)
        self.assertIn("Test project analysis", result)
        self.assertIn("Related Classes/Methods", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst_with_badges(self):
        # Test RST generation includes CodeBoarding badges
        result = generate_rst(
            self.analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=self.linked_files,
            demo=False,
            file_name="test",
        )

        self.assertIn("|codeboarding-badge|", result)
        self.assertIn("|demo-badge|", result)
        self.assertIn("|contact-badge|", result)
        self.assertIn(".. |codeboarding-badge| image::", result)
        self.assertIn("https://img.shields.io/badge/", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst_details_section(self):
        # Test that Details section is properly formatted
        result = generate_rst(
            self.analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=self.linked_files,
            demo=False,
            file_name="test",
        )

        self.assertIn("Details", result)
        self.assertIn("-------", result)  # Section underline

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst_file(self):
        # Test RST file generation
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            result_file = generate_rst_file(
                file_name="test_file",
                insights=self.analysis,
                project=self.project,
                repo_ref=self.repo_ref,
                linked_files=self.linked_files,
                temp_dir=temp_path,
                demo=False,
            )

            self.assertTrue(result_file.exists())
            self.assertEqual(result_file.name, "test_file.rst")

            content = result_file.read_text()
            self.assertIn("Component1", content)
            self.assertIn("Component2", content)

    def test_generate_rst_with_no_references(self):
        # Test RST generation for component with no source code references
        component_no_ref = Component(
            name="NoRefComponent",
            description="Component with no references",
            referenced_source_code=[],
        )

        analysis_no_ref = AnalysisInsights(
            description="Test analysis",
            components=[component_no_ref],
            components_relations=[],
        )

        result = generate_rst(
            analysis_no_ref,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=[],
            demo=False,
            file_name="test",
        )

        self.assertIn("NoRefComponent", result)
        self.assertIn("*None*", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst_with_line_numbers(self):
        # Test that line numbers are included in RST links
        result = generate_rst(
            self.analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=self.linked_files,
            demo=False,
            file_name="test",
        )

        self.assertIn("#L10-L20", result)
        self.assertIn("#L5-L15", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst_with_reference_no_file(self):
        # Test RST with reference that has no file
        ref_no_file = SourceCodeReference(
            qualified_name="test.module",
            reference_file=None,
            reference_start_line=10,
            reference_end_line=20,
        )

        component = Component(
            name="TestComp",
            description="Test",
            referenced_source_code=[ref_no_file],
        )

        analysis = AnalysisInsights(
            description="Test",
            components=[component],
            components_relations=[],
        )

        result = generate_rst(
            analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=[],
            demo=False,
            file_name="test",
        )

        # Should handle gracefully - no crash
        self.assertIn("TestComp", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst_with_invalid_line_numbers(self):
        # Test RST with reference that has invalid line numbers (same start and end)
        ref_invalid = SourceCodeReference(
            qualified_name="test.module",
            reference_file="/repo/test/module.py",
            reference_start_line=10,
            reference_end_line=10,
        )

        component = Component(
            name="TestComp",
            description="Test",
            referenced_source_code=[ref_invalid],
        )

        analysis = AnalysisInsights(
            description="Test",
            components=[component],
            components_relations=[],
        )

        result = generate_rst(
            analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=[],
            demo=False,
            file_name="test",
        )

        # Should not include line numbers for invalid ranges
        self.assertIn("test.module", result)
        # Line numbers should be filtered out for invalid ranges
        self.assertNotIn("#L10-L10", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_rst_reference_outside_root(self):
        # Test RST with reference outside repo root
        ref_outside = SourceCodeReference(
            qualified_name="external.module",
            reference_file="/external/module.py",
            reference_start_line=1,
            reference_end_line=10,
        )

        component = Component(
            name="ExternalComp",
            description="Uses external code",
            referenced_source_code=[ref_outside],
        )

        analysis = AnalysisInsights(
            description="Test",
            components=[component],
            components_relations=[],
        )

        result = generate_rst(
            analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=[],
            demo=False,
            file_name="test",
        )

        self.assertIn("external.module", result)
        # Should handle reference without creating invalid URL


if __name__ == "__main__":
    unittest.main()
