import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FilePath,
    Relation,
    RelationEdge,
    SourceCodeReference,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, Edge
from static_analyzer.node import Node
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin


class ConcreteReferenceResolver(ReferenceResolverMixin):
    """Concrete implementation for testing the mixin"""

    def __init__(self, repo_dir, static_analysis):
        super().__init__(repo_dir, static_analysis)
        self.mock_parse_invoke = Mock()

    # Expose the protected helper for tests
    def _try_llm_resolution(self, reference, qname, file_candidates=None):
        return self._parse_invoke(reference, qname)

    def _parse_invoke(self, prompt, type):
        """Implementation of abstract method for testing"""
        return self.mock_parse_invoke(prompt, type)


class TestReferenceResolverMixin(unittest.TestCase):
    def setUp(self):
        # Create temporary directory
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir)

        # Create some test files
        (self.repo_dir / "test.py").write_text("class TestClass:\n    pass\n")
        (self.repo_dir / "module").mkdir()
        (self.repo_dir / "module" / "file.py").write_text("def test_function():\n    pass\n")
        (self.repo_dir / "nested").mkdir()
        (self.repo_dir / "nested" / "deep").mkdir()
        (self.repo_dir / "nested" / "deep" / "module.py").write_text("def deep_function():\n    pass\n")

        # Create mock static analysis
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]

        # Create resolver instance
        self.resolver = ConcreteReferenceResolver(repo_dir=self.repo_dir, static_analysis=self.mock_static_analysis)

    def tearDown(self):
        # Clean up
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fix_source_code_reference_lines_already_resolved(self):
        """Test that already resolved references with existing files are skipped"""
        # Create reference with existing absolute path
        existing_file = str(self.repo_dir / "test.py")
        reference = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file=existing_file,
            reference_start_line=1,
            reference_end_line=2,
        )

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[reference],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        # Should not try to resolve since file exists
        self.assertEqual(reference.reference_file, "test.py")  # Should be converted to relative path

    def test_fix_source_code_reference_lines_resolves_relation_key_edges(self):
        source_node = Node("test.TestClass", NodeType.FUNCTION, str(self.repo_dir / "test.py"), 1, 2)
        source_node.file_path = str(self.repo_dir / "test.py")
        target_node = Node(
            "module.file.test_function", NodeType.FUNCTION, str(self.repo_dir / "module" / "file.py"), 1, 2
        )
        target_node.file_path = str(self.repo_dir / "module" / "file.py")
        cfg = CallGraph(edges=[Edge(source_node, target_node, [{"line": 2, "column": 5}])])

        self.mock_static_analysis.get_reference.side_effect = [source_node, target_node]
        self.mock_static_analysis.get_cfg.return_value = cfg
        source_component = Component(name="Source", description="", key_entities=[])
        target_component = Component(name="Target", description="", key_entities=[])
        relation = Relation(
            relation="dispatches to",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="test.TestClass"),
                    target=SourceCodeReference(qualified_name="module.file.test_function"),
                    description="dispatches through registry",
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test",
            components=[source_component, target_component],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        edge = result.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.reference_file, "test.py")
        self.assertEqual(edge.source.reference_start_line, 1)
        self.assertEqual(edge.target.reference_file, os.path.join("module", "file.py"))
        self.assertEqual(edge.target.reference_start_line, 1)
        self.assertEqual(edge.call_sites, [{"line": 2, "column": 5}])

    def test_fix_source_code_reference_lines_keeps_key_edge_without_static_edge(self):
        source_node = Node("test.TestClass", NodeType.FUNCTION, str(self.repo_dir / "test.py"), 1, 2)
        target_node = Node(
            "module.file.test_function", NodeType.FUNCTION, str(self.repo_dir / "module" / "file.py"), 1, 2
        )

        self.mock_static_analysis.get_reference.side_effect = [source_node, target_node]
        self.mock_static_analysis.get_cfg.return_value = CallGraph()
        source_component = Component(name="Source", description="", key_entities=[])
        target_component = Component(name="Target", description="", key_entities=[])
        relation = Relation(
            relation="dispatches to",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="test.TestClass"),
                    target=SourceCodeReference(qualified_name="module.file.test_function"),
                    description="dispatches through registry",
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test",
            components=[source_component, target_component],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        edge = result.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.reference_file, "test.py")
        self.assertEqual(edge.target.reference_file, os.path.join("module", "file.py"))
        self.assertEqual(edge.call_sites, [])

    def test_fix_source_code_reference_lines_keeps_external_target_edge(self):
        source_node = Node("service.OCR.extract_text", NodeType.FUNCTION, str(self.repo_dir / "test.py"), 1, 2)
        self.mock_static_analysis.get_reference.side_effect = [source_node, ValueError("not found")]
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = [source_node]
        self.mock_static_analysis.get_cfg.return_value = CallGraph()
        source_component = Component(name="Source", description="", key_entities=[])
        target_component = Component(name="Target", description="", key_entities=[])
        relation = Relation(
            relation="calls external LLM API",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="service.OCR.extract_text"),
                    target=SourceCodeReference(qualified_name="openai.OpenAI"),
                    description="External OpenAI client call",
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test",
            components=[source_component, target_component],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        edge = result.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.reference_file, "test.py")
        self.assertEqual(edge.target.qualified_name, "openai.OpenAI")
        self.assertIsNone(edge.target.reference_file)
        self.assertEqual(edge.call_sites, [])

    def test_fix_source_code_reference_lines_drops_described_relation_when_key_edges_do_not_resolve(self):
        self.mock_static_analysis.get_reference.side_effect = ValueError("not found")
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = []
        source_component = Component(name="Source", description="", key_entities=[])
        target_component = Component(name="Target", description="", key_entities=[])
        relation = Relation(
            relation="provides data to",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="missing.Source"),
                    target=SourceCodeReference(qualified_name="missing.Target"),
                    description="runtime hook",
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test",
            components=[source_component, target_component],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(result.components_relations, [])

    def test_fix_source_code_reference_lines_keeps_evidence_backed_relation_when_key_edges_do_not_resolve(self):
        self.mock_static_analysis.get_reference.side_effect = ValueError("not found")
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = []
        source_component = Component(name="Source", description="", key_entities=[])
        target_component = Component(name="Target", description="", key_entities=[])
        relation = Relation(
            relation="provides data to",
            src_name="Source",
            dst_name="Target",
            evidence="Runtime registry dispatch in Source.configure wires Target without a direct CFG edge.",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="missing.Source"),
                    target=SourceCodeReference(qualified_name="missing.Target"),
                    description="runtime hook",
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test",
            components=[source_component, target_component],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        relation = result.components_relations[0]
        self.assertEqual(relation.key_edges, [])
        self.assertEqual(
            relation.evidence, "Runtime registry dispatch in Source.configure wires Target without a direct CFG edge."
        )

    def test_fix_source_code_reference_lines_drops_unsupported_relation_without_evidence(self):
        self.mock_static_analysis.get_reference.side_effect = ValueError("not found")
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = []
        source_component = Component(name="Source", description="", key_entities=[])
        target_component = Component(name="Target", description="", key_entities=[])
        relation = Relation(
            relation="provides data to",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(qualified_name="missing.Source"),
                    target=SourceCodeReference(qualified_name="missing.Target"),
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test",
            components=[source_component, target_component],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(result.components_relations, [])

    def test_symbol_token_match_resolves_unique_final_token(self):
        node = Node(
            "packages.markitdown.src.markitdown._markitdown.MarkItDown.enable_builtins",
            NodeType.FUNCTION,
            str(self.repo_dir / "test.py"),
            10,
            20,
        )
        self.mock_static_analysis.get_reference.side_effect = ValueError("not found")
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = [node]
        reference = SourceCodeReference(qualified_name="markitdown.MarkItDown:enable_builtins")

        self.resolver._resolve_single_reference(reference)

        self.assertEqual(reference.qualified_name, node.fully_qualified_name)
        self.assertEqual(reference.reference_file, str(self.repo_dir / "test.py"))
        self.assertEqual(reference.reference_start_line, 10)

    def test_symbol_token_match_uses_backwards_tokens_to_disambiguate(self):
        markitdown_node = Node(
            "packages.markitdown.src.markitdown._markitdown.MarkItDown.enable_builtins",
            NodeType.FUNCTION,
            str(self.repo_dir / "test.py"),
            10,
            20,
        )
        other_node = Node(
            "packages.other.src.other.Other.enable_builtins",
            NodeType.FUNCTION,
            str(self.repo_dir / "module" / "file.py"),
            30,
            40,
        )
        self.mock_static_analysis.get_reference.side_effect = ValueError("not found")
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = [other_node, markitdown_node]
        reference = SourceCodeReference(qualified_name="markitdown.MarkItDown:enable_builtins")

        self.resolver._resolve_single_reference(reference)

        self.assertEqual(reference.qualified_name, markitdown_node.fully_qualified_name)

    def test_symbol_token_match_leaves_ambiguous_references_unresolved(self):
        first = Node("pkg.one.Service.run", NodeType.FUNCTION, str(self.repo_dir / "test.py"), 1, 2)
        second = Node("pkg.two.Service.run", NodeType.FUNCTION, str(self.repo_dir / "module" / "file.py"), 3, 4)
        self.mock_static_analysis.get_reference.side_effect = ValueError("not found")
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = [first, second]
        reference = SourceCodeReference(qualified_name="Service:run")

        self.resolver._resolve_single_reference(reference)

        self.assertIsNone(reference.reference_file)

    def test_symbol_token_match_respects_explicit_module_token(self):
        wrong_module = Node(
            "packages.markitdown.src.markitdown.converters._cu_converter._is_analyzer_compatible",
            NodeType.FUNCTION,
            str(self.repo_dir / "module" / "file.py"),
            3,
            4,
        )
        self.mock_static_analysis.get_reference.side_effect = ValueError("not found")
        self.mock_static_analysis.get_loose_reference.return_value = ("", None)
        self.mock_static_analysis.iter_reference_nodes.return_value = [wrong_module]
        reference = SourceCodeReference(
            qualified_name="packages.markitdown.src.markitdown.converters._doc_intel_converter._is_analyzer_compatible"
        )

        self.resolver._resolve_single_reference(reference)

        self.assertIsNone(reference.reference_file)

    def test_fix_source_code_reference_lines_drops_same_endpoint_key_edge(self):
        existing_file = str(self.repo_dir / "test.py")
        source_component = Component(name="Source", description="", key_entities=[])
        target_component = Component(name="Target", description="", key_entities=[])
        relation = Relation(
            relation="provides data to",
            src_name="Source",
            dst_name="Target",
            key_edges=[
                RelationEdge(
                    source=SourceCodeReference(
                        qualified_name="test.TestClass",
                        reference_file=existing_file,
                        reference_start_line=1,
                        reference_end_line=2,
                    ),
                    target=SourceCodeReference(
                        qualified_name="test.TestClass",
                        reference_file=existing_file,
                        reference_start_line=1,
                        reference_end_line=2,
                    ),
                    description="self edge",
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test",
            components=[source_component, target_component],
            components_relations=[relation],
        )

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(result.components_relations, [])

    def test_try_exact_match_success(self):
        """Test exact reference matching succeeds"""
        reference = SourceCodeReference(
            qualified_name="test.TestClass", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock get_reference to return a node
        mock_node = MagicMock()
        mock_node.file_path = str(self.repo_dir / "test.py")
        mock_node.line_start = 1
        mock_node.line_end = 3
        self.mock_static_analysis.get_reference.return_value = mock_node

        result = self.resolver._try_exact_match(reference, "test.TestClass", "python")

        self.assertTrue(result)
        self.assertEqual(reference.reference_file, str(self.repo_dir / "test.py"))
        self.assertEqual(reference.reference_start_line, 1)
        self.assertEqual(reference.reference_end_line, 3)

    def test_try_exact_match_failure(self):
        """Test exact reference matching fails gracefully"""
        reference = SourceCodeReference(
            qualified_name="nonexistent.Class", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        self.mock_static_analysis.get_reference.side_effect = ValueError("Not found")

        result = self.resolver._try_exact_match(reference, "nonexistent.Class", "python")

        self.assertFalse(result)
        self.assertIsNone(reference.reference_file)

    def test_try_loose_match_success(self):
        """Test loose reference matching succeeds"""
        reference = SourceCodeReference(
            qualified_name="TestClass", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock get_loose_reference to return a node
        mock_node = MagicMock()
        mock_node.file_path = str(self.repo_dir / "test.py")
        mock_node.line_start = 1
        mock_node.line_end = 3
        self.mock_static_analysis.get_loose_reference.return_value = ("test.TestClass", mock_node)

        result = self.resolver._try_loose_match(reference, "TestClass", "python")

        self.assertTrue(result)
        self.assertEqual(reference.reference_file, str(self.repo_dir / "test.py"))

    def test_try_loose_match_failure(self):
        """Test loose reference matching fails gracefully"""
        reference = SourceCodeReference(
            qualified_name="NonExistent", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        self.mock_static_analysis.get_loose_reference.side_effect = Exception("Not found")

        result = self.resolver._try_loose_match(reference, "NonExistent", "python")

        self.assertFalse(result)

    def test_try_existing_reference_file_relative_path(self):
        """Test resolution with existing relative reference file path"""
        reference = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",  # Relative path
            reference_start_line=1,
            reference_end_line=2,
        )

        result = self.resolver._try_existing_reference_file(reference, "python")

        self.assertTrue(result)
        self.assertEqual(reference.reference_file, str(self.repo_dir / "test.py"))

    def test_try_existing_reference_file_nonexistent(self):
        """Test resolution with nonexistent reference file path"""
        reference = SourceCodeReference(
            qualified_name="nonexistent.Class",
            reference_file="nonexistent.py",
            reference_start_line=1,
            reference_end_line=2,
        )

        result = self.resolver._try_existing_reference_file(reference, "python")

        self.assertFalse(result)
        self.assertIsNone(reference.reference_file)  # Should be cleared

    def test_try_qualified_name_as_path_with_file_ref_pattern(self):
        """Test resolving qualified name using the file_ref pattern (converts last separator to dot)"""
        # The file_ref pattern converts /repo/module/file -> /repo/module.file
        # So create a file at /repo/module.file
        (self.repo_dir / "module.file").write_text("# test content\n")

        reference = SourceCodeReference(
            qualified_name="module.file", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        result = self.resolver._try_qualified_name_as_path(reference, "module.file", "python")

        self.assertTrue(result)
        # Should find via the file_ref pattern
        self.assertIsNotNone(reference.reference_file)
        assert reference.reference_file is not None
        self.assertTrue(reference.reference_file.endswith("module.file"))

    def test_try_qualified_name_as_path_full_path_match(self):
        """Test resolving qualified name as full path directory"""
        # Create a directory matching the full path
        nested_dir = self.repo_dir / "nested" / "deep" / "module"
        nested_dir.mkdir(parents=True)

        reference = SourceCodeReference(
            qualified_name="nested.deep.module",
            reference_file=None,
            reference_start_line=None,
            reference_end_line=None,
        )

        result = self.resolver._try_qualified_name_as_path(reference, "nested.deep.module", "python")

        self.assertTrue(result)
        # Should find the directory path
        self.assertIsNotNone(reference.reference_file)
        assert reference.reference_file is not None
        self.assertTrue(reference.reference_file.endswith(os.path.join("nested", "deep", "module")))

    def test_relative_paths_conversion(self):
        """Test conversion of absolute paths to relative paths"""
        abs_path = str(self.repo_dir / "test.py")
        reference = SourceCodeReference(
            qualified_name="test.TestClass", reference_file=abs_path, reference_start_line=1, reference_end_line=2
        )

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[reference],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        result = self.resolver._relative_paths(analysis)

        # Should convert to relative path
        self.assertEqual(reference.reference_file, "test.py")

    def test_relative_paths_preserves_non_repo_paths(self):
        """Test that paths outside repo are preserved"""
        external_path = "/some/external/path.py"
        reference = SourceCodeReference(
            qualified_name="external.Module",
            reference_file=external_path,
            reference_start_line=1,
            reference_end_line=2,
        )

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[reference],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        result = self.resolver._relative_paths(analysis)

        # Should preserve external path
        self.assertEqual(reference.reference_file, external_path)

    def test_resolve_single_reference_cascade(self):
        """Test that reference resolution tries strategies in order"""
        reference = SourceCodeReference(
            qualified_name="module.file.test_function",
            reference_file=None,
            reference_start_line=None,
            reference_end_line=None,
        )

        # Make exact and loose match fail
        self.mock_static_analysis.get_reference.side_effect = ValueError("Not found")
        self.mock_static_analysis.get_loose_reference.side_effect = Exception("Not found")

        # Should fall back to file candidate matching: qname prefix "module.file" matches "module/file.py"
        self.resolver._resolve_single_reference(reference, ["module/file.py"])

        expected_path = str(self.repo_dir / "module" / "file.py")
        self.assertEqual(reference.reference_file, expected_path)

    def test_symbol_match_across_languages_beats_existing_file_fallback(self):
        """A weaker file-path match must not preempt a later language symbol match."""
        (self.repo_dir / "src").mkdir()
        (self.repo_dir / "src" / "widget.ts").write_text("export class Widget {}\n")
        self.mock_static_analysis.get_languages.return_value = ["python", "typescript"]

        reference = SourceCodeReference(
            qualified_name="src.widget.Widget",
            reference_file="src/widget.ts",
            reference_start_line=None,
            reference_end_line=None,
        )

        ts_node = MagicMock()
        ts_node.file_path = str(self.repo_dir / "src" / "widget.ts")
        ts_node.line_start = 12
        ts_node.line_end = 34
        ts_node.fully_qualified_name = "src.widget.Widget"
        self.mock_static_analysis.get_reference.side_effect = [ValueError("Not in python"), ts_node]

        self.resolver._resolve_single_reference(reference)

        self.assertEqual(reference.reference_file, str(self.repo_dir / "src" / "widget.ts"))
        self.assertEqual(reference.reference_start_line, 12)
        self.assertEqual(reference.reference_end_line, 34)
        self.mock_static_analysis.get_loose_reference.assert_not_called()

    def test_resolve_single_reference_cascade_no_match(self):
        """Test that unrelated file candidates are NOT used as fallback"""
        reference = SourceCodeReference(
            qualified_name="totally.unrelated.ClassName",
            reference_file=None,
            reference_start_line=None,
            reference_end_line=None,
        )

        # Make exact and loose match fail
        self.mock_static_analysis.get_reference.side_effect = ValueError("Not found")
        self.mock_static_analysis.get_loose_reference.side_effect = Exception("Not found")

        # File candidate doesn't match the qname — should remain unresolved
        self.resolver._resolve_single_reference(reference, ["module/file.py"])

        self.assertIsNone(reference.reference_file)

    def test_fix_source_code_reference_lines_multiple_languages(self):
        """Test resolution across multiple languages"""
        self.mock_static_analysis.get_languages.return_value = ["python", "typescript"]

        reference = SourceCodeReference(
            qualified_name="test.TestClass", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[reference],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        # Make python to fail, should try typescript
        self.mock_static_analysis.get_reference.side_effect = [
            ValueError("Not in python"),
            ValueError("Not in typescript"),
        ]
        self.mock_static_analysis.get_loose_reference.side_effect = [
            Exception("Not in python"),
            Exception("Not in typescript"),
        ]

        # Mock LLM resolution as final fallback
        mock_file_path = FilePath(file_path="test.py", start_line=1, end_line=2)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        result = self.resolver.fix_source_code_reference_lines(analysis)

        # Should have attempted both languages; no LLM fallback
        self.assertEqual(self.mock_static_analysis.get_reference.call_count, 2)

    def test_remove_unresolved_references(self):
        """Test that unresolved references are removed after resolution attempts"""
        # Create a mix of resolved and unresolved references
        resolved_ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file=str(self.repo_dir / "test.py"),
            reference_start_line=1,
            reference_end_line=2,
        )

        unresolved_ref_none = SourceCodeReference(
            qualified_name="nonexistent.Class",
            reference_file=None,  # Never resolved
            reference_start_line=None,
            reference_end_line=None,
        )

        unresolved_ref_invalid = SourceCodeReference(
            qualified_name="invalid.path",
            reference_file="nonexistent_file.py",  # Resolved but file doesn't exist
            reference_start_line=1,
            reference_end_line=2,
        )

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[resolved_ref, unresolved_ref_none, unresolved_ref_invalid],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        # Call the cleanup method
        self.resolver._remove_unresolved_references(analysis)

        # Only resolved reference should remain
        self.assertEqual(len(component.key_entities), 1)
        self.assertEqual(component.key_entities[0].qualified_name, "test.TestClass")
        self.assertEqual(component.key_entities[0].reference_file, str(self.repo_dir / "test.py"))

    def test_fix_source_code_reference_lines_removes_unresolved(self):
        """Test that fix_source_code_reference_lines removes unresolved references after resolution"""
        # Create references where some will fail resolution
        good_ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",  # Will be resolved to absolute path
            reference_start_line=1,
            reference_end_line=2,
        )

        bad_ref = SourceCodeReference(
            qualified_name="nonexistent.Class",
            reference_file=None,
            reference_start_line=None,
            reference_end_line=None,
        )

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[good_ref, bad_ref],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        # Make all resolution strategies fail for bad_ref
        self.mock_static_analysis.get_reference.side_effect = ValueError("Not found")
        self.mock_static_analysis.get_loose_reference.side_effect = Exception("Not found")

        # Mock LLM to return nonexistent file
        mock_file_path = FilePath(file_path="nonexistent.py", start_line=1, end_line=2)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        result = self.resolver.fix_source_code_reference_lines(analysis)

        # Only the good reference should remain (converted to relative path)
        self.assertEqual(len(component.key_entities), 1)
        self.assertEqual(component.key_entities[0].qualified_name, "test.TestClass")
        self.assertEqual(component.key_entities[0].reference_file, "test.py")

    def test_remove_unresolved_references_multiple_components(self):
        """Test that unresolved references are removed from multiple components"""
        # Component 1: mix of resolved and unresolved
        comp1_resolved = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file=str(self.repo_dir / "test.py"),
            reference_start_line=1,
            reference_end_line=2,
        )
        comp1_unresolved = SourceCodeReference(
            qualified_name="bad.ref",
            reference_file=None,
            reference_start_line=None,
            reference_end_line=None,
        )

        component1 = Component(
            name="Component1",
            description="Test 1",
            key_entities=[comp1_resolved, comp1_unresolved],
        )

        # Component 2: all unresolved
        comp2_unresolved1 = SourceCodeReference(
            qualified_name="bad.ref1",
            reference_file=None,
            reference_start_line=None,
            reference_end_line=None,
        )
        comp2_unresolved2 = SourceCodeReference(
            qualified_name="bad.ref2",
            reference_file="nonexistent.py",
            reference_start_line=1,
            reference_end_line=2,
        )

        component2 = Component(
            name="Component2",
            description="Test 2",
            key_entities=[comp2_unresolved1, comp2_unresolved2],
        )

        # Component 3: all resolved
        comp3_resolved = SourceCodeReference(
            qualified_name="module.file",
            reference_file=str(self.repo_dir / "module" / "file.py"),
            reference_start_line=1,
            reference_end_line=2,
        )

        component3 = Component(
            name="Component3",
            description="Test 3",
            key_entities=[comp3_resolved],
        )

        analysis = AnalysisInsights(
            description="Test", components=[component1, component2, component3], components_relations=[]
        )

        # Call the cleanup method
        self.resolver._remove_unresolved_references(analysis)

        # Component 1 should have 1 reference
        self.assertEqual(len(component1.key_entities), 1)
        self.assertEqual(component1.key_entities[0].qualified_name, "test.TestClass")

        # Component 2 should have 0 references
        self.assertEqual(len(component2.key_entities), 0)

        # Component 3 should still have 1 reference
        self.assertEqual(len(component3.key_entities), 1)
        self.assertEqual(component3.key_entities[0].qualified_name, "module.file")


if __name__ == "__main__":
    unittest.main()
