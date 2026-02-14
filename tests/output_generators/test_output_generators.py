import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    SourceCodeReference,
    assign_component_ids,
)
from utils import sanitize
from output_generators.html import (
    component_header_html,
    generate_cytoscape_data,
    generate_html,
    generate_html_file,
)
from output_generators.markdown import (
    component_header,
    generate_markdown,
    generate_markdown_file,
    generated_mermaid_str,
)


class TestOutputGeneratorsSanitize(unittest.TestCase):
    def test_sanitize_alphanumeric(self):
        # Test with alphanumeric string (no change expected)
        result = sanitize("Component123")
        self.assertEqual(result, "Component123")

    def test_sanitize_with_spaces(self):
        # Test with spaces
        result = sanitize("User Management")
        self.assertEqual(result, "User_Management")

    def test_sanitize_with_special_chars(self):
        # Test with special characters
        result = sanitize("Data-Base@Handler!")
        self.assertEqual(result, "Data_Base_Handler_")

    def test_sanitize_multiple_special_chars(self):
        # Test with consecutive special characters
        result = sanitize("Test:::Component")
        self.assertEqual(result, "Test_Component")


class TestMarkdownGenerator(unittest.TestCase):
    def setUp(self):
        # Create sample components for testing
        self.comp1 = Component(
            name="Authentication",
            description="Handles user authentication",
            key_entities=[],
            assigned_files=[],
        )
        self.comp2 = Component(name="Database", description="Database layer", key_entities=[], assigned_files=[])

        # Create sample relations
        self.rel1 = Relation(src_name="Authentication", dst_name="Database", relation="uses")

        # Create sample insights
        self.insights = AnalysisInsights(
            description="Test application architecture",
            components=[self.comp1, self.comp2],
            components_relations=[self.rel1],
        )
        assign_component_ids(self.insights)

    def test_generated_mermaid_str_basic(self):
        # Test basic mermaid string generation
        result = generated_mermaid_str(
            self.insights, expanded_components=set(), repo_ref="", project="test", demo=False
        )

        self.assertIn("```mermaid", result)
        self.assertIn("graph LR", result)
        self.assertIn("Authentication", result)
        self.assertIn("Database", result)
        self.assertIn('Authentication -- "uses" --> Database', result)

    def test_generated_mermaid_str_with_links(self):
        # Test with expanded components
        expanded = {self.comp1.component_id}
        result = generated_mermaid_str(self.insights, expanded_components=expanded, repo_ref="/repo", project="test")

        self.assertIn('click Authentication href "/repo/Authentication.md"', result)

    def test_generated_mermaid_str_demo_mode(self):
        # Test demo mode links
        expanded = {self.comp1.component_id}
        result = generated_mermaid_str(
            self.insights, expanded_components=expanded, repo_ref="", project="myproject", demo=True
        )

        self.assertIn("https://github.com/CodeBoarding/GeneratedOnBoardings", result)
        self.assertIn("myproject/Authentication.md", result)

    def test_generate_markdown(self):
        # Test full markdown generation
        result = generate_markdown(self.insights, project="test", repo_ref="/repo", expanded_components=set())

        self.assertIn("```mermaid", result)
        self.assertIn("## Details", result)
        self.assertIn(self.insights.description, result)
        self.assertIn("Authentication", result)
        self.assertIn("Database", result)
        self.assertIn("CodeBoarding", result)  # Badge

    def test_generate_markdown_with_source_references(self):
        # Test with source code references
        ref = SourceCodeReference(
            qualified_name="auth.service.AuthService",
            reference_file="auth/service.py",
            reference_start_line=10,
            reference_end_line=20,
        )
        comp_with_ref = Component(name="Auth", description="Auth component", assigned_files=[], key_entities=[ref])
        insights = AnalysisInsights(description="Test", components=[comp_with_ref], components_relations=[])

        with patch.dict("os.environ", {"REPO_ROOT": ""}):
            with patch("os.path.exists", return_value=True):
                result = generate_markdown(
                    insights, project="", repo_ref="https://github.com/test/", expanded_components=set()
                )

                self.assertIn("Related Classes/Methods", result)
                self.assertIn("AuthService", result)
                self.assertIn("#L10-L20", result)

    def test_generate_markdown_file(self):
        # Test markdown file generation
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            result_path = generate_markdown_file(
                "test_output",
                self.insights,
                project="test",
                repo_ref="/repo",
                expanded_components=set(),
                temp_dir=temp_path,
            )

            self.assertTrue(result_path.exists())
            self.assertEqual(result_path.name, "test_output.md")

            content = result_path.read_text()
            self.assertIn("```mermaid", content)

    def test_component_header_with_link(self):
        # Test component header with link
        expanded = {"test_comp_id"}  # matches the component_id passed below
        result = component_header("TestComponent", "test_comp_id", expanded)

        self.assertIn("TestComponent", result)
        self.assertIn("[[Expand]]", result)
        self.assertIn("TestComponent.md", result)

    def test_component_header_without_link(self):
        # Test component header without link
        result = component_header("TestComponent", "test_comp_id", set())

        self.assertIn("TestComponent", result)
        self.assertNotIn("[[Expand]]", result)


class TestHTMLGenerator(unittest.TestCase):
    def setUp(self):
        # Create sample components for testing
        self.comp1 = Component(
            name="Authentication",
            description="Handles user authentication",
            key_entities=[],
            assigned_files=[],
        )
        self.comp2 = Component(name="Database", description="Database layer", key_entities=[], assigned_files=[])

        # Create sample relations
        self.rel1 = Relation(src_name="Authentication", dst_name="Database", relation="uses")

        # Create sample insights
        self.insights = AnalysisInsights(
            description="Test application architecture",
            components=[self.comp1, self.comp2],
            components_relations=[self.rel1],
        )
        assign_component_ids(self.insights)

    def test_generate_cytoscape_data(self):
        # Test Cytoscape data generation
        result = generate_cytoscape_data(self.insights, expanded_components=set(), project="test", demo=False)

        self.assertIn("elements", result)
        elements = result["elements"]

        # Should have 2 nodes + 1 edge = 3 elements
        self.assertEqual(len(elements), 3)

        # Check node data
        node_ids = {elem["data"]["id"] for elem in elements if "source" not in elem["data"]}
        self.assertIn("Authentication", node_ids)
        self.assertIn("Database", node_ids)

    def test_generate_cytoscape_data_with_links(self):
        # Test with expanded components
        expanded = {self.comp1.component_id}
        result = generate_cytoscape_data(self.insights, expanded_components=expanded, project="test", demo=False)

        # Find the Authentication node
        auth_node = next(elem for elem in result["elements"] if elem["data"]["id"] == "Authentication")

        self.assertTrue(auth_node["data"]["hasLink"])
        self.assertIn("linkUrl", auth_node["data"])
        self.assertEqual(auth_node["data"]["linkUrl"], "./Authentication.html")

    def test_generate_cytoscape_data_demo_mode(self):
        # Test demo mode
        expanded = {self.comp1.component_id}
        result = generate_cytoscape_data(self.insights, expanded_components=expanded, project="myproject", demo=True)

        auth_node = next(elem for elem in result["elements"] if elem["data"]["id"] == "Authentication")

        self.assertIn("github.com/CodeBoarding", auth_node["data"]["linkUrl"])

    def test_generate_cytoscape_data_edges(self):
        # Test edge generation
        result = generate_cytoscape_data(self.insights, expanded_components=set(), project="test", demo=False)

        edges = [elem for elem in result["elements"] if "source" in elem["data"]]
        self.assertEqual(len(edges), 1)

        edge = edges[0]
        self.assertEqual(edge["data"]["source"], "Authentication")
        self.assertEqual(edge["data"]["target"], "Database")
        self.assertEqual(edge["data"]["label"], "uses")

    def test_generate_cytoscape_data_skip_invalid_edges(self):
        # Test that invalid edges are skipped
        invalid_rel = Relation(src_name="NonExistent", dst_name="Database", relation="uses")
        insights = AnalysisInsights(description="Test", components=[self.comp2], components_relations=[invalid_rel])

        result = generate_cytoscape_data(insights, expanded_components=set(), project="test", demo=False)

        # Should have 1 node and 0 edges (invalid edge skipped)
        edges = [elem for elem in result["elements"] if "source" in elem["data"]]
        self.assertEqual(len(edges), 0)

    def test_generate_html(self):
        # Test HTML generation
        result = generate_html(self.insights, project="test", repo_ref="", expanded_components=set())

        self.assertIn("<html", result.lower())
        self.assertIn("Authentication", result)
        self.assertIn("Database", result)
        self.assertIn("Handles user authentication", result)

    def test_generate_html_with_references(self):
        # Test HTML with source code references
        ref = SourceCodeReference(
            qualified_name="auth.service.AuthService",
            reference_file="auth/service.py",
            reference_start_line=10,
            reference_end_line=20,
        )
        comp_with_ref = Component(name="Auth", description="Auth component", assigned_files=[], key_entities=[ref])
        insights = AnalysisInsights(description="Test", components=[comp_with_ref], components_relations=[])

        with patch.dict("os.environ", {"REPO_ROOT": ""}):
            result = generate_html(insights, project="", repo_ref="https://github.com/test/", expanded_components=set())

            self.assertIn("Related Classes/Methods", result)
            self.assertIn("AuthService", result)

    def test_generate_html_file(self):
        # Test HTML file generation
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            result_path = generate_html_file(
                "test_output",
                self.insights,
                project="test",
                repo_ref="",
                expanded_components=set(),
                temp_dir=temp_path,
            )

            self.assertTrue(result_path.exists())
            self.assertEqual(result_path.name, "test_output.html")

            content = result_path.read_text()
            self.assertIn("<html", content.lower())

    def test_component_header_html_with_link(self):
        # Test HTML component header with link
        expanded = {"test_comp_id"}
        result = component_header_html("TestComponent", "test_comp_id", expanded)

        self.assertIn("TestComponent", result)
        self.assertIn("[Expand]", result)
        self.assertIn('href="./TestComponent.html"', result)

    def test_component_header_html_without_link(self):
        # Test HTML component header without link
        result = component_header_html("TestComponent", "test_comp_id", set())

        self.assertIn("TestComponent", result)
        self.assertNotIn("[Expand]", result)


if __name__ == "__main__":
    unittest.main()
