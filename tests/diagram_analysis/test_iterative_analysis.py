"""Tests for iterative analysis functionality."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.change_classifier import (
    ChangeClassifier,
    ChangeClassificationResult,
    ChangeType,
    ClassifiedChange,
    classify_change_for_file,
)
from diagram_analysis.version import IterativeAnalysisMetadata, Version
from repo_utils.file_hash import compute_hash, normalize_content, detect_moves
from static_analyzer.symbol_diff import SymbolDiffAnalyzer, SymbolExtractor, SymbolInfo


class TestContentHashing(unittest.TestCase):
    """Tests for file content hashing."""

    def test_normalize_content_line_endings(self):
        """Test that line endings are normalized."""
        content_crlf = "line1\r\nline2\r\nline3"
        content_lf = "line1\nline2\nline3"
        content_cr = "line1\rline2\rline3"

        self.assertEqual(normalize_content(content_crlf), normalize_content(content_lf))
        self.assertEqual(normalize_content(content_cr), normalize_content(content_lf))

    def test_normalize_content_trailing_whitespace(self):
        """Test that trailing whitespace is removed."""
        content_with_ws = "line1   \nline2\t\t\nline3  "
        content_clean = "line1\nline2\nline3"

        self.assertEqual(normalize_content(content_with_ws), content_clean)

    def test_normalize_content_blank_lines(self):
        """Test that multiple blank lines are collapsed."""
        content_many_blanks = "line1\n\n\n\nline2\n\n\nline3"
        normalized = normalize_content(content_many_blanks)

        # Count blank lines
        blank_count = sum(1 for line in normalized.split("\n") if not line.strip())
        self.assertEqual(blank_count, 2)  # One after line1, one after line2

    def test_compute_hash_deterministic(self):
        """Test that hashing is deterministic."""
        content = "def foo():\n    pass\n"
        hash1 = compute_hash(content)
        hash2 = compute_hash(content)
        self.assertEqual(hash1, hash2)

    def test_compute_hash_different_for_different_content(self):
        """Test that different content produces different hashes."""
        content1 = "def foo():\n    pass\n"
        content2 = "def bar():\n    pass\n"
        self.assertNotEqual(compute_hash(content1), compute_hash(content2))

    def test_compute_hash_same_for_whitespace_differences(self):
        """Test that whitespace differences don't affect hash."""
        content1 = "def foo():  \n    pass\n\n"
        content2 = "def foo():\n    pass"
        self.assertEqual(compute_hash(content1), compute_hash(content2))


class TestMoveDetection(unittest.TestCase):
    """Tests for file move detection."""

    def test_detect_simple_rename(self):
        """Test detection of a simple file rename."""
        old_hashes = {"old/file.py": "abc123"}
        new_hashes = {"new/file.py": "abc123"}

        moves, unmatched_deleted, unmatched_added = detect_moves(
            deleted_files=["old/file.py"],
            added_files=["new/file.py"],
            old_hashes=old_hashes,
            new_hashes=new_hashes,
        )

        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0], ("old/file.py", "new/file.py"))
        self.assertEqual(len(unmatched_deleted), 0)
        self.assertEqual(len(unmatched_added), 0)

    def test_detect_no_move_different_extension(self):
        """Test that files with different extensions are not considered moves."""
        old_hashes = {"old/file.py": "abc123"}
        new_hashes = {"new/file.ts": "abc123"}

        moves, unmatched_deleted, unmatched_added = detect_moves(
            deleted_files=["old/file.py"],
            added_files=["new/file.ts"],
            old_hashes=old_hashes,
            new_hashes=new_hashes,
        )

        self.assertEqual(len(moves), 0)
        self.assertEqual(len(unmatched_deleted), 1)
        self.assertEqual(len(unmatched_added), 1)

    def test_detect_no_move_different_content(self):
        """Test that files with different content are not considered moves."""
        old_hashes = {"old/file.py": "abc123"}
        new_hashes = {"new/file.py": "def456"}

        moves, unmatched_deleted, unmatched_added = detect_moves(
            deleted_files=["old/file.py"],
            added_files=["new/file.py"],
            old_hashes=old_hashes,
            new_hashes=new_hashes,
        )

        self.assertEqual(len(moves), 0)


class TestSymbolExtractor(unittest.TestCase):
    """Tests for symbol extraction."""

    def setUp(self):
        self.extractor = SymbolExtractor()

    def test_extract_python_class(self):
        """Test extraction of Python class."""
        content = """class MyClass:
    def method(self):
        pass
"""
        symbols = self.extractor.extract_symbols("test.py", content)
        class_symbols = [s for s in symbols if s.name == "MyClass"]
        self.assertEqual(len(class_symbols), 1)

    def test_extract_python_function(self):
        """Test extraction of Python function."""
        content = """def my_function(arg1, arg2):
    return arg1 + arg2
"""
        symbols = self.extractor.extract_symbols("test.py", content)
        func_symbols = [s for s in symbols if s.name == "my_function"]
        self.assertEqual(len(func_symbols), 1)
        self.assertIn("arg1", func_symbols[0].signature)

    def test_extract_async_function(self):
        """Test extraction of async Python function."""
        content = """async def async_function(x):
    await something()
    return x
"""
        symbols = self.extractor.extract_symbols("test.py", content)
        func_symbols = [s for s in symbols if s.name == "async_function"]
        self.assertEqual(len(func_symbols), 1)


class TestSymbolDiff(unittest.TestCase):
    """Tests for symbol-level diffing."""

    def setUp(self):
        self.analyzer = SymbolDiffAnalyzer()

    def test_detect_added_function(self):
        """Test detection of added function."""
        old_content = """def foo():
    pass
"""
        new_content = """def foo():
    pass

def bar():
    pass
"""
        diff = self.analyzer.diff_symbols("test.py", old_content, new_content)
        self.assertTrue(diff.has_api_changes)
        self.assertEqual(len(diff.added_symbols), 1)
        self.assertEqual(diff.added_symbols[0].name, "bar")

    def test_detect_removed_function(self):
        """Test detection of removed function."""
        old_content = """def foo():
    pass

def bar():
    pass
"""
        new_content = """def foo():
    pass
"""
        diff = self.analyzer.diff_symbols("test.py", old_content, new_content)
        self.assertTrue(diff.has_api_changes)
        self.assertEqual(len(diff.removed_symbols), 1)
        self.assertEqual(diff.removed_symbols[0].name, "bar")

    def test_detect_implementation_only_change(self):
        """Test detection of implementation-only change."""
        old_content = """def foo():
    pass
"""
        new_content = """def foo():
    x = 1
    y = 2
    return x + y
"""
        diff = self.analyzer.diff_symbols("test.py", old_content, new_content)
        self.assertFalse(diff.has_api_changes)
        self.assertEqual(len(diff.implementation_only), 1)


class TestIterativeAnalysisMetadata(unittest.TestCase):
    """Tests for IterativeAnalysisMetadata."""

    def test_get_changed_files_added(self):
        """Test detection of added files."""
        metadata = IterativeAnalysisMetadata(
            commit_hash="abc123",
            file_content_hashes={"file1.py": "hash1"},
        )
        new_hashes = {"file1.py": "hash1", "file2.py": "hash2"}

        added, modified, deleted = metadata.get_changed_files(new_hashes)

        self.assertEqual(added, ["file2.py"])
        self.assertEqual(modified, [])
        self.assertEqual(deleted, [])

    def test_get_changed_files_modified(self):
        """Test detection of modified files."""
        metadata = IterativeAnalysisMetadata(
            commit_hash="abc123",
            file_content_hashes={"file1.py": "hash1"},
        )
        new_hashes = {"file1.py": "hash2"}

        added, modified, deleted = metadata.get_changed_files(new_hashes)

        self.assertEqual(added, [])
        self.assertEqual(modified, ["file1.py"])
        self.assertEqual(deleted, [])

    def test_get_changed_files_deleted(self):
        """Test detection of deleted files."""
        metadata = IterativeAnalysisMetadata(
            commit_hash="abc123",
            file_content_hashes={"file1.py": "hash1", "file2.py": "hash2"},
        )
        new_hashes = {"file1.py": "hash1"}

        added, modified, deleted = metadata.get_changed_files(new_hashes)

        self.assertEqual(added, [])
        self.assertEqual(modified, [])
        self.assertEqual(deleted, ["file2.py"])


class TestChangeClassifier(unittest.TestCase):
    """Tests for change classification."""

    def test_classify_new_file(self):
        """Test classification of new file."""
        change_type = classify_change_for_file(
            file_path="new_file.py",
            old_content=None,
            new_content="def foo(): pass",
        )
        self.assertEqual(change_type, ChangeType.NEW_FILE)

    def test_classify_deleted_file(self):
        """Test classification of deleted file."""
        change_type = classify_change_for_file(
            file_path="old_file.py",
            old_content="def foo(): pass",
            new_content=None,
        )
        self.assertEqual(change_type, ChangeType.DELETED)

    def test_classify_cosmetic_change(self):
        """Test classification of cosmetic (whitespace) change."""
        old_content = "def foo():  \n    pass\n\n"
        new_content = "def foo():\n    pass"

        change_type = classify_change_for_file(
            file_path="file.py",
            old_content=old_content,
            new_content=new_content,
        )
        self.assertEqual(change_type, ChangeType.COSMETIC)

    def test_classify_structural_change(self):
        """Test classification of structural (API) change."""
        old_content = "def foo(): pass"
        new_content = "def foo(): pass\ndef bar(): pass"

        change_type = classify_change_for_file(
            file_path="file.py",
            old_content=old_content,
            new_content=new_content,
        )
        self.assertEqual(change_type, ChangeType.STRUCTURAL)


class TestChangeClassificationResult(unittest.TestCase):
    """Tests for ChangeClassificationResult."""

    def test_has_changes_with_no_changes(self):
        """Test has_changes returns False when only cosmetic changes exist."""
        result = ChangeClassificationResult()
        result.cosmetic_changes = [ClassifiedChange(file_path="f.py", change_type=ChangeType.COSMETIC)]
        self.assertFalse(result.has_changes)

    def test_has_changes_with_internal(self):
        """Test has_changes returns True with internal changes."""
        result = ChangeClassificationResult()
        result.internal_changes = [ClassifiedChange(file_path="f.py", change_type=ChangeType.INTERNAL)]
        self.assertTrue(result.has_changes)

    def test_requires_llm_with_structural(self):
        """Test requires_llm returns True with structural changes."""
        result = ChangeClassificationResult()
        result.structural_changes = [ClassifiedChange(file_path="f.py", change_type=ChangeType.STRUCTURAL)]
        self.assertTrue(result.requires_llm)

    def test_summary(self):
        """Test summary generation."""
        result = ChangeClassificationResult()
        result.cosmetic_changes = [ClassifiedChange(file_path="f1.py", change_type=ChangeType.COSMETIC)]
        result.new_files = [ClassifiedChange(file_path="f2.py", change_type=ChangeType.NEW_FILE)]

        summary = result.summary()
        self.assertIn("1 cosmetic", summary)
        self.assertIn("1 new files", summary)


if __name__ == "__main__":
    unittest.main()
