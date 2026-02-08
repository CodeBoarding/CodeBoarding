"""Tests for unused_code_diagnostics health check."""

import pytest

from health.checks.unused_code_diagnostics import (
    LSPDiagnosticsCollector,
    DeadCodeCategory,
    DiagnosticIssue,
    check_unused_code_diagnostics,
    get_category_description,
)
from health.models import Severity
from static_analyzer.lsp_client.client import LSPDiagnostic


def _make_diagnostic(
    code: str,
    message: str,
    line: int,
    character: int = 0,
    end_line: int | None = None,
    severity: int = 2,
    tags: list[int] | None = None,
) -> LSPDiagnostic:
    """Helper to create an LSPDiagnostic from a raw dict, matching old test patterns."""
    return LSPDiagnostic.from_lsp_dict(
        {
            "code": code,
            "message": message,
            "range": {
                "start": {"line": line, "character": character},
                "end": {"line": end_line if end_line is not None else line, "character": 10},
            },
            "severity": severity,
            "tags": tags if tags is not None else [1],
        }
    )


class TestDeadCodeCategory:
    """Tests for DeadCodeCategory enum."""

    def test_category_values(self):
        """Test that all categories have correct string values."""
        assert DeadCodeCategory.UNUSED_IMPORT.value == "unused_import"
        assert DeadCodeCategory.UNUSED_VARIABLE.value == "unused_variable"
        assert DeadCodeCategory.UNUSED_FUNCTION.value == "unused_function"
        assert DeadCodeCategory.UNUSED_CLASS.value == "unused_class"
        assert DeadCodeCategory.UNUSED_PARAMETER.value == "unused_parameter"
        assert DeadCodeCategory.DEAD_CODE.value == "dead_code"
        assert DeadCodeCategory.UNREACHABLE_CODE.value == "unreachable_code"
        assert DeadCodeCategory.UNKNOWN.value == "unknown"


class TestLSPDiagnosticsCollector:
    """Tests for LSPDiagnosticsCollector class."""

    def test_collector_initialization(self):
        """Test that collector initializes empty."""
        collector = LSPDiagnosticsCollector()
        assert collector.diagnostics == []
        assert collector.issues == []

    def test_add_diagnostic(self):
        """Test adding a diagnostic."""
        collector = LSPDiagnosticsCollector()
        diagnostic = _make_diagnostic("reportUnusedImport", "Import is unused", line=0)
        collector.add_diagnostic("/path/to/file.py", diagnostic)
        assert len(collector.diagnostics) == 1
        assert collector.diagnostics[0].file_path == "/path/to/file.py"

    def test_process_diagnostics_unused_import(self):
        """Test processing diagnostics for unused import."""
        collector = LSPDiagnosticsCollector()
        diagnostic = _make_diagnostic("reportUnusedImport", "Import is unused", line=5, end_line=5)
        collector.add_diagnostic("/path/to/file.py", diagnostic)
        issues = collector.process_diagnostics()

        assert len(issues) == 1
        assert issues[0].category == DeadCodeCategory.UNUSED_IMPORT
        assert issues[0].file_path == "/path/to/file.py"
        assert issues[0].line_start == 5

    def test_process_diagnostics_unused_variable(self):
        """Test processing diagnostics for unused variable."""
        collector = LSPDiagnosticsCollector()
        diagnostic = _make_diagnostic(
            "reportUnusedVariable", "Variable is not accessed", line=10, character=4, end_line=10
        )
        collector.add_diagnostic("/path/to/file.py", diagnostic)
        issues = collector.process_diagnostics()

        assert len(issues) == 1
        assert issues[0].category == DeadCodeCategory.UNUSED_VARIABLE

    def test_process_diagnostics_unknown_category(self):
        """Test that diagnostics with unknown codes are skipped."""
        collector = LSPDiagnosticsCollector()
        diagnostic = _make_diagnostic("someRandomError", "Some error", line=0, severity=1, tags=[])
        collector.add_diagnostic("/path/to/file.py", diagnostic)
        issues = collector.process_diagnostics()

        assert len(issues) == 0

    def test_process_diagnostics_deduplication(self):
        """Test that duplicate diagnostics are deduplicated."""
        collector = LSPDiagnosticsCollector()
        diagnostic = _make_diagnostic("reportUnusedImport", "Import is unused", line=5, end_line=5)
        # Add the same diagnostic twice
        collector.add_diagnostic("/path/to/file.py", diagnostic)
        collector.add_diagnostic("/path/to/file.py", diagnostic)
        issues = collector.process_diagnostics()

        assert len(issues) == 1  # Should be deduplicated

    def test_get_issues_by_category(self):
        """Test grouping issues by category."""
        collector = LSPDiagnosticsCollector()

        # Add import diagnostic
        collector.add_diagnostic(
            "/path/to/file1.py",
            _make_diagnostic("reportUnusedImport", "Import unused", line=0),
        )

        # Add variable diagnostic
        collector.add_diagnostic(
            "/path/to/file2.py",
            _make_diagnostic("reportUnusedVariable", "Variable unused", line=5),
        )

        collector.process_diagnostics()
        issues_by_category = collector.get_issues_by_category()

        assert DeadCodeCategory.UNUSED_IMPORT in issues_by_category
        assert DeadCodeCategory.UNUSED_VARIABLE in issues_by_category
        assert len(issues_by_category[DeadCodeCategory.UNUSED_IMPORT]) == 1
        assert len(issues_by_category[DeadCodeCategory.UNUSED_VARIABLE]) == 1


class TestCheckUnusedCodeDiagnostics:
    """Tests for check_unused_code_diagnostics function."""

    def test_empty_collector(self):
        """Test with no diagnostics collected."""
        collector = LSPDiagnosticsCollector()
        summary = check_unused_code_diagnostics(collector)

        assert summary.check_name == "unused_code_diagnostics"
        assert summary.total_entities_checked == 0
        assert summary.findings_count == 0
        assert summary.score == 1.0  # Perfect score when no issues

    def test_with_issues(self):
        """Test with some diagnostic issues."""
        collector = LSPDiagnosticsCollector()

        # Add a few diagnostics
        for i in range(3):
            collector.add_diagnostic(
                f"/path/to/file{i}.py",
                _make_diagnostic("reportUnusedImport", f"Import {i} unused", line=i),
            )

        summary = check_unused_code_diagnostics(collector)

        assert summary.total_entities_checked == 3
        assert summary.findings_count == 3
        assert summary.score < 1.0  # Score should be reduced
        assert len(summary.finding_groups) > 0

    def test_score_calculation(self):
        """Test that score is calculated correctly based on number of issues."""
        collector = LSPDiagnosticsCollector()

        # Add 5 diagnostics
        for i in range(5):
            collector.add_diagnostic(
                f"/path/to/file{i}.py",
                _make_diagnostic("reportUnusedImport", f"Import {i} unused", line=i),
            )

        summary = check_unused_code_diagnostics(collector)
        # Score should be 1.0 - (5 * 0.05) = 0.75
        assert summary.score == 0.75


class TestGetCategoryDescription:
    """Tests for get_category_description function."""

    def test_all_categories_have_descriptions(self):
        """Test that all categories have non-empty descriptions."""
        for category in DeadCodeCategory:
            description = get_category_description(category)
            assert description is not None
            assert len(description) > 0
            assert isinstance(description, str)

    def test_unused_import_description(self):
        """Test specific description for unused import."""
        description = get_category_description(DeadCodeCategory.UNUSED_IMPORT)
        assert "import" in description.lower()

    def test_unknown_category_description(self):
        """Test description for unknown category."""
        description = get_category_description(DeadCodeCategory.UNKNOWN)
        assert "unknown" in description.lower() or "potentially" in description.lower()
