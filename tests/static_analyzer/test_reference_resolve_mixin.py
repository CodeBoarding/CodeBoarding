import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from agents.agent_responses import AnalysisInsights, Component, FilePath, SourceCodeReference
from static_analyzer.analysis_result import StaticAnalysisResults
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
        import shutil

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
            assigned_files=["test.py"],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        # Should not try to resolve since file exists
        self.assertEqual(reference.reference_file, "test.py")  # Should be converted to relative path

    def test_try_exact_match_success(self):
        """Test exact reference matching succeeds"""
        reference = SourceCodeReference(
            qualified_name="test.TestClass", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock get_reference to return a node
        mock_node = MagicMock()
        mock_node.file_path = str(self.repo_dir / "test.py")
        mock_node.line_start = 0
        mock_node.line_end = 2
        self.mock_static_analysis.get_reference.return_value = mock_node

        result = self.resolver._try_exact_match(reference, "test.TestClass", "python")

        self.assertTrue(result)
        self.assertEqual(reference.reference_file, str(self.repo_dir / "test.py"))
        self.assertEqual(reference.reference_start_line, 1)  # 1-based indexing
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
        mock_node.line_start = 0
        mock_node.line_end = 2
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
        self.assertTrue(reference.reference_file.endswith("nested/deep/module"))

    def test_llm_resolution_with_relative_path(self):
        """Test LLM resolution normalizes relative paths to absolute"""
        reference = SourceCodeReference(
            qualified_name="test.TestClass", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock LLM to return relative path
        mock_file_path = FilePath(file_path="test.py", start_line=1, end_line=2)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        self.resolver._try_llm_resolution(reference, "test.TestClass", ["test.py"])

        # Should normalize to absolute path
        self.assertEqual(reference.reference_file, str(self.repo_dir / "test.py"))
        self.assertEqual(reference.reference_start_line, 1)
        self.assertEqual(reference.reference_end_line, 2)

    def test_llm_resolution_with_filename_only(self):
        """Test LLM resolution finds file by name recursively"""
        # Create a uniquely named file in nested directory
        unique_file = self.repo_dir / "nested" / "unique_test.py"
        unique_file.write_text("# test content\n")

        reference = SourceCodeReference(
            qualified_name="unique_test", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock LLM to return just filename
        mock_file_path = FilePath(file_path="unique_test.py", start_line=1, end_line=1)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        self.resolver._try_llm_resolution(reference, "unique_test", ["unique_test.py"])

        # Should find the file recursively and convert to absolute path
        assert reference.reference_file is not None
        self.assertTrue(reference.reference_file.endswith("unique_test.py"))
        self.assertTrue(os.path.isabs(reference.reference_file))
        self.assertTrue(os.path.exists(reference.reference_file))

    def test_llm_resolution_with_absolute_path(self):
        """Test LLM resolution keeps absolute paths as-is"""
        reference = SourceCodeReference(
            qualified_name="test.TestClass", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock LLM to return absolute path
        abs_path = str(self.repo_dir / "test.py")
        mock_file_path = FilePath(file_path=abs_path, start_line=1, end_line=2)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        self.resolver._try_llm_resolution(reference, "test.TestClass", ["test.py"])

        # Should keep absolute path unchanged
        self.assertEqual(reference.reference_file, abs_path)

    def test_llm_resolution_with_nonexistent_file(self):
        """Test LLM resolution handles nonexistent files"""
        reference = SourceCodeReference(
            qualified_name="nonexistent.Class", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock LLM to return nonexistent file
        mock_file_path = FilePath(file_path="nonexistent.py", start_line=1, end_line=2)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        self.resolver._try_llm_resolution(reference, "nonexistent.Class", ["nonexistent.py"])

        # The reference should be None to signal resolution failure
        self.assertIsNone(reference.reference_file)

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
            assigned_files=["test.py"],
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
            assigned_files=[],
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

        # Mock LLM resolution to return a valid FilePath object
        mock_file_path = FilePath(file_path="module/file.py", start_line=1, end_line=10)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        # Should fall back to LLM resolution after file path resolution fails
        self.resolver._resolve_single_reference(reference, ["module/file.py"])

        # Should resolve via LLM strategy and normalize the path
        self.assertIsNotNone(reference.reference_file)
        # Since module/file.py exists, it should be converted to absolute path
        expected_abs_path = str(self.repo_dir / "module" / "file.py")
        self.assertEqual(reference.reference_file, expected_abs_path)

    def test_llm_resolution_with_ambiguous_filename(self):
        """Test LLM resolution handles ambiguous file names (multiple matches)"""
        # Create multiple files with the same name in different directories
        (self.repo_dir / "dir1").mkdir()
        (self.repo_dir / "dir1" / "common.py").write_text("# dir1 version\n")
        (self.repo_dir / "dir2").mkdir()
        (self.repo_dir / "dir2" / "common.py").write_text("# dir2 version\n")

        reference = SourceCodeReference(
            qualified_name="common", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock LLM to return just filename (ambiguous)
        mock_file_path = FilePath(file_path="common.py", start_line=1, end_line=1)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        self.resolver._try_llm_resolution(reference, "common", ["common.py"])

        # Should fail to resolve due to ambiguity and set reference_file to None
        self.assertIsNone(reference.reference_file)

    def test_llm_resolution_with_unique_filename_in_subdirectory(self):
        """Test LLM resolution succeeds with unique file name in subdirectory"""
        # Create a uniquely named file in nested directory
        (self.repo_dir / "subdir1").mkdir()
        (self.repo_dir / "subdir1" / "unique_file.py").write_text("# unique content\n")

        reference = SourceCodeReference(
            qualified_name="unique_file", reference_file=None, reference_start_line=None, reference_end_line=None
        )

        # Mock LLM to return just filename (unambiguous - only one match)
        mock_file_path = FilePath(file_path="unique_file.py", start_line=1, end_line=1)
        self.resolver.mock_parse_invoke.return_value = mock_file_path

        self.resolver._try_llm_resolution(reference, "unique_file", ["unique_file.py"])

        # Should successfully resolve to the unique match
        self.assertIsNotNone(reference.reference_file)
        assert reference.reference_file is not None  # needed for mypy
        self.assertTrue(reference.reference_file.endswith("unique_file.py"))
        self.assertTrue(os.path.exists(reference.reference_file))

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
            assigned_files=["test.py"],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        # Mock python to fail, should try typescript
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

        # Should have attempted both languages before falling back to LLM
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
            assigned_files=["test.py"],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        # Call the cleanup method
        self.resolver._remove_unresolved_references(analysis)

        # Only resolved reference should remain
        self.assertEqual(len(component.key_entities), 1)
        self.assertEqual(component.key_entities[0].qualified_name, "test.TestClass")
        self.assertEqual(component.key_entities[0].reference_file, str(self.repo_dir / "test.py"))

    def test_remove_unresolved_assigned_files(self):
        """Test that unresolved assigned files are removed"""
        resolved_ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file=str(self.repo_dir / "test.py"),
            reference_start_line=1,
            reference_end_line=2,
        )

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[resolved_ref],
            assigned_files=[
                "test.py",  # Exists (relative path)
                "module/file.py",  # Exists (relative path)
                "nonexistent.py",  # Doesn't exist
                "also_nonexistent/file.py",  # Doesn't exist
            ],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        # Call the cleanup method
        self.resolver._remove_unresolved_references(analysis)

        # Only existing files should remain
        self.assertEqual(len(component.assigned_files), 2)
        self.assertIn("test.py", component.assigned_files)
        self.assertIn("module/file.py", component.assigned_files)
        self.assertNotIn("nonexistent.py", component.assigned_files)
        self.assertNotIn("also_nonexistent/file.py", component.assigned_files)

    def test_remove_unresolved_assigned_files_absolute_paths(self):
        """Test that assigned files with absolute paths are handled correctly"""
        resolved_ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file=str(self.repo_dir / "test.py"),
            reference_start_line=1,
            reference_end_line=2,
        )

        abs_existing_path = str(self.repo_dir / "test.py")
        abs_nonexistent_path = "/some/nonexistent/path.py"

        component = Component(
            name="TestComponent",
            description="Test",
            key_entities=[resolved_ref],
            assigned_files=[
                abs_existing_path,  # Absolute path that exists
                abs_nonexistent_path,  # Absolute path that doesn't exist
                "module/file.py",  # Relative path that exists
            ],
        )

        analysis = AnalysisInsights(description="Test", components=[component], components_relations=[])

        # Call the cleanup method
        self.resolver._remove_unresolved_references(analysis)

        # Only existing files should remain
        self.assertEqual(len(component.assigned_files), 2)
        self.assertIn(abs_existing_path, component.assigned_files)
        self.assertIn("module/file.py", component.assigned_files)
        self.assertNotIn(abs_nonexistent_path, component.assigned_files)

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
            assigned_files=["test.py"],
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
        """Test that unresolved references and assigned files are removed from multiple components"""
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
            assigned_files=["test.py", "nonexistent1.py"],
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
            assigned_files=["nonexistent2.py", "nonexistent3.py"],
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
            assigned_files=["module/file.py", "test.py"],
        )

        analysis = AnalysisInsights(
            description="Test", components=[component1, component2, component3], components_relations=[]
        )

        # Call the cleanup method
        self.resolver._remove_unresolved_references(analysis)

        # Component 1 should have 1 reference and 1 assigned file
        self.assertEqual(len(component1.key_entities), 1)
        self.assertEqual(component1.key_entities[0].qualified_name, "test.TestClass")
        self.assertEqual(len(component1.assigned_files), 1)
        self.assertIn("test.py", component1.assigned_files)

        # Component 2 should have 0 references and 0 assigned files
        self.assertEqual(len(component2.key_entities), 0)
        self.assertEqual(len(component2.assigned_files), 0)

        # Component 3 should still have 1 reference and 2 assigned files
        self.assertEqual(len(component3.key_entities), 1)
        self.assertEqual(component3.key_entities[0].qualified_name, "module.file")
        self.assertEqual(len(component3.assigned_files), 2)
        self.assertIn("module/file.py", component3.assigned_files)
        self.assertIn("test.py", component3.assigned_files)


if __name__ == "__main__":
    unittest.main()
