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
from output_generators.mdx import (
    component_header,
    generate_frontmatter,
    generate_mdx,
    generate_mdx_file,
    generated_mermaid_str,
)


class TestMDXOutput(unittest.TestCase):
    def setUp(self):
        # Create test data
        self.ref1 = SourceCodeReference(
            qualified_name="test.Component1.method",
            reference_file="/repo/test/component1.py",
            reference_start_line=10,
            reference_end_line=20,
        )

        self.ref2 = SourceCodeReference(
            qualified_name="test.Component2.function",
            reference_file="/repo/test/component2.py",
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

    def test_generate_frontmatter_onboarding(self):
        # Test frontmatter generation for onboarding file
        result = generate_frontmatter("on_boarding")

        self.assertIn("title:", result)
        self.assertIn("Architecture Overview", result)
        self.assertIn("description:", result)
        self.assertIn("icon:", result)

    def test_generate_frontmatter_analysis(self):
        # Test frontmatter generation for analysis file
        result = generate_frontmatter("analysis")

        self.assertIn("title:", result)
        self.assertIn("Architecture Overview", result)

    def test_generate_frontmatter_component(self):
        # Test frontmatter generation for component file
        result = generate_frontmatter("test_component", component_name="Test Component")

        self.assertEqual(result, "# Test Component")

    def test_generated_mermaid_str(self):
        # Test Mermaid diagram generation
        result = generated_mermaid_str(
            self.analysis,
            linked_files=self.linked_files,
            repo_ref=self.repo_ref,
            project=self.project,
            demo=False,
        )

        self.assertIn("```mermaid", result)
        self.assertIn("graph LR", result)
        self.assertIn("Component1", result)
        self.assertIn("Component2", result)
        self.assertIn("depends on", result)
        self.assertIn("```", result)

    def test_generated_mermaid_str_with_links(self):
        # Test Mermaid diagram with links
        result = generated_mermaid_str(
            self.analysis,
            linked_files=self.linked_files,
            repo_ref=self.repo_ref,
            project=self.project,
            demo=False,
        )

        self.assertIn("click", result)
        self.assertIn("/codeboarding/", result)

    def test_component_header_with_link(self):
        # Test component header with link
        result = component_header("Component1", self.linked_files, demo=True)

        self.assertIn("Component1", result)
        self.assertIn("Expand", result)
        self.assertIn("component1", result.lower())

    def test_component_header_without_link(self):
        # Test component header without link
        result = component_header("Component1", self.linked_files, demo=False)

        self.assertIn("Component1", result)
        self.assertNotIn("Expand", result)

    def test_component_header_no_linked_files(self):
        # Test component header when component not in linked files
        result = component_header("UnlinkedComponent", self.linked_files, demo=True)

        self.assertIn("UnlinkedComponent", result)
        self.assertNotIn("Expand", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos"})
    def test_generate_mdx(self):
        # Test full MDX generation
        result = generate_mdx(
            self.analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=self.linked_files,
            demo=False,
            file_name="on_boarding",
        )

        self.assertIn("title:", result)
        self.assertIn("```mermaid", result)
        self.assertIn("Component1", result)
        self.assertIn("Component2", result)
        self.assertIn("Test project analysis", result)
        self.assertIn("Related Classes/Methods", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos"})
    def test_generate_mdx_with_info_component(self):
        # Test MDX generation includes Info component for onboarding
        result = generate_mdx(
            self.analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=self.linked_files,
            demo=False,
            file_name="on_boarding",
        )

        self.assertIn("<Info>", result)
        self.assertIn("CodeBoarding", result)
        self.assertIn("</Info>", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos"})
    def test_generate_mdx_component_file(self):
        # Test MDX generation for component file
        result = generate_mdx(
            self.analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=self.linked_files,
            demo=False,
            file_name="component_name",
        )

        self.assertIn("# component name", result)
        self.assertNotIn("<Info>", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos"})
    def test_generate_mdx_file(self):
        # Test MDX file generation
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            result_file = generate_mdx_file(
                file_name="test_file",
                insights=self.analysis,
                project=self.project,
                repo_ref=self.repo_ref,
                linked_files=self.linked_files,
                temp_dir=temp_path,
                demo=False,
            )

            self.assertTrue(result_file.exists())
            self.assertEqual(result_file.name, "test_file.mdx")

            content = result_file.read_text()
            self.assertIn("Component1", content)
            self.assertIn("Component2", content)

    def test_generate_mdx_with_no_references(self):
        # Test MDX generation for component with no source code references
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

        result = generate_mdx(
            analysis_no_ref,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=[],
            demo=False,
            file_name="test",
        )

        self.assertIn("NoRefComponent", result)
        self.assertIn("_None_", result)

    @patch.dict(os.environ, {"REPO_ROOT": "/repo"})
    def test_generate_mdx_with_line_numbers(self):
        # Test that line numbers are included in links
        result = generate_mdx(
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
    def test_generate_mdx_with_reference_no_lines(self):
        # Test MDX with reference that has no line numbers
        ref_no_lines = SourceCodeReference(
            qualified_name="test.module",
            reference_file="/repo/test/module.py",
            reference_start_line=None,
            reference_end_line=None,
        )

        component = Component(
            name="TestComp",
            description="Test",
            referenced_source_code=[ref_no_lines],
        )

        analysis = AnalysisInsights(
            description="Test",
            components=[component],
            components_relations=[],
        )

        result = generate_mdx(
            analysis,
            project=self.project,
            repo_ref=self.repo_ref,
            linked_files=[],
            demo=False,
            file_name="test",
        )

        self.assertIn("test.module", result)
        # Should not include line number links
        self.assertNotIn("#L", result)


if __name__ == "__main__":
    unittest.main()
