"""Tests for unused_code_diagnostics health check."""

import pytest

from health.checks.unused_code_diagnostics import (
    DIAGNOSTIC_CODE_MAPPINGS,
    LSPDiagnosticsCollector,
    DeadCodeCategory,
    DiagnosticIssue,
    check_unused_code_diagnostics,
    get_category_description,
)
from health.models import Severity
from static_analyzer.lsp_client.diagnostics import LSPDiagnostic


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
                "end": {
                    "line": end_line if end_line is not None else line,
                    "character": 10,
                },
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


class TestLSPDiagnosticCodeExtraction:
    """Tests for LSPDiagnostic.from_lsp_dict handling of code formats."""

    def test_string_code_preserved(self):
        """Plain string codes (older LSP servers) are preserved as-is."""
        diag = LSPDiagnostic.from_lsp_dict(
            {"code": "reportUnusedImport", "message": "unused", "range": {"start": {"line": 0}, "end": {"line": 0}}}
        )
        assert diag.code == "reportUnusedImport"

    def test_int_code_converted_to_string(self):
        """Integer codes (e.g. TypeScript error numbers) are converted to strings."""
        diag = LSPDiagnostic.from_lsp_dict(
            {"code": 6133, "message": "unused", "range": {"start": {"line": 0}, "end": {"line": 0}}}
        )
        assert diag.code == "6133"

    def test_missing_code_defaults_to_empty(self):
        """Missing code field defaults to empty string."""
        diag = LSPDiagnostic.from_lsp_dict({"message": "unused", "range": {"start": {"line": 0}, "end": {"line": 0}}})
        assert diag.code == ""


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
        assert issues[0].line_start == 6  # LSP 0-based line 5 -> 1-based line 6

    def test_process_diagnostics_unused_variable(self):
        """Test processing diagnostics for unused variable."""
        collector = LSPDiagnosticsCollector()
        diagnostic = _make_diagnostic(
            "reportUnusedVariable",
            "Variable is not accessed",
            line=10,
            character=4,
            end_line=10,
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

    def test_process_diagnostics_deduplication_cross_code(self):
        """Test that diagnostics with different codes but same category and line are deduplicated.

        Pyright can report the same unused variable twice: once via reportUnusedVariable
        and once via the Unnecessary tag with a different message.
        """
        collector = LSPDiagnosticsCollector()
        # Diagnostic via explicit code
        diag1 = _make_diagnostic(
            "reportUnusedVariable",
            'Variable "node_name" is not accessed',
            line=10,
        )
        # Diagnostic via Unnecessary tag (no specific code, just tag=1)
        diag2 = _make_diagnostic(
            "",
            '"node_name" is not accessed',
            line=10,
            tags=[1],
        )
        collector.add_diagnostic("/path/to/file.py", diag1)
        collector.add_diagnostic("/path/to/file.py", diag2)
        issues = collector.process_diagnostics()

        assert len(issues) == 1  # Should be deduplicated by category + line

    def test_process_diagnostics_line_numbers_are_1_based(self):
        """Test that LSP 0-based line numbers are converted to 1-based."""
        collector = LSPDiagnosticsCollector()
        diagnostic = _make_diagnostic("reportUnusedVariable", "Variable unused", line=0, end_line=0)
        collector.add_diagnostic("/path/to/file.py", diagnostic)
        issues = collector.process_diagnostics()

        assert len(issues) == 1
        assert issues[0].line_start == 1  # 0-based line 0 -> 1-based line 1
        assert issues[0].line_end == 1

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


# ---------------------------------------------------------------------------
# Line number accuracy across all diagnostic types
# ---------------------------------------------------------------------------


class TestDiagnosticLineNumberAccuracy:
    """Verify that 0-based LSP line numbers are consistently converted to 1-based
    across every category of diagnostic."""

    @pytest.mark.parametrize(
        "code,category,lsp_line,expected_line",
        [
            ("reportUnusedImport", DeadCodeCategory.UNUSED_IMPORT, 0, 1),
            ("reportUnusedImport", DeadCodeCategory.UNUSED_IMPORT, 4, 5),
            ("reportUnusedVariable", DeadCodeCategory.UNUSED_VARIABLE, 9, 10),
            ("reportUnusedFunction", DeadCodeCategory.UNUSED_FUNCTION, 99, 100),
            ("reportUnusedClass", DeadCodeCategory.UNUSED_CLASS, 0, 1),
            ("reportUnusedParameter", DeadCodeCategory.UNUSED_PARAMETER, 14, 15),
            ("reportUnreachable", DeadCodeCategory.UNREACHABLE_CODE, 50, 51),
        ],
        ids=[
            "import-line-0",
            "import-line-4",
            "variable-line-9",
            "function-line-99",
            "class-line-0",
            "parameter-line-14",
            "unreachable-line-50",
        ],
    )
    def test_line_conversion_per_category(self, code, category, lsp_line, expected_line):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic(code, "unused", line=lsp_line, end_line=lsp_line),
        )
        issues = collector.process_diagnostics()
        assert len(issues) == 1
        assert issues[0].line_start == expected_line
        assert issues[0].line_end == expected_line
        assert issues[0].category == category

    def test_multiline_diagnostic_range(self):
        """A diagnostic spanning lines 10-15 (0-based) should become 11-16 (1-based)."""
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("reportUnusedFunction", "unused function", line=10, end_line=15),
        )
        issues = collector.process_diagnostics()
        assert issues[0].line_start == 11
        assert issues[0].line_end == 16


# ---------------------------------------------------------------------------
# Multi-language diagnostic code mappings
# ---------------------------------------------------------------------------


class TestMultiLanguageDiagnosticCodes:
    """Verify that diagnostic codes from all supported LSP servers are correctly mapped."""

    @pytest.mark.parametrize(
        "code,expected_category",
        [
            # Pyright / Pylance (Python)
            ("reportUnusedImport", DeadCodeCategory.UNUSED_IMPORT),
            ("reportUnusedVariable", DeadCodeCategory.UNUSED_VARIABLE),
            ("reportUnusedFunction", DeadCodeCategory.UNUSED_FUNCTION),
            ("reportUnusedClass", DeadCodeCategory.UNUSED_CLASS),
            ("reportUnusedParameter", DeadCodeCategory.UNUSED_PARAMETER),
            ("reportUnreachable", DeadCodeCategory.UNREACHABLE_CODE),
            # TypeScript / JavaScript
            ("6133", DeadCodeCategory.UNUSED_VARIABLE),
            ("6138", DeadCodeCategory.UNUSED_PARAMETER),
            ("6196", DeadCodeCategory.UNUSED_IMPORT),
            ("noUnusedLocals", DeadCodeCategory.UNUSED_VARIABLE),
            ("noUnusedParameters", DeadCodeCategory.UNUSED_PARAMETER),
            # gopls (Go)
            ("unusedparams", DeadCodeCategory.UNUSED_PARAMETER),
            ("unusedvariable", DeadCodeCategory.UNUSED_VARIABLE),
            # Intelephense (PHP)
            ("unusedUseStatement", DeadCodeCategory.UNUSED_IMPORT),
            ("P1001", DeadCodeCategory.UNUSED_VARIABLE),
            ("P1002", DeadCodeCategory.UNUSED_IMPORT),
            # Eclipse JDT (Java)
            ("org.eclipse.jdt.core.compiler.problem.unusedImport", DeadCodeCategory.UNUSED_IMPORT),
            ("org.eclipse.jdt.core.compiler.problem.unusedLocal", DeadCodeCategory.UNUSED_VARIABLE),
            ("org.eclipse.jdt.core.compiler.problem.deadCode", DeadCodeCategory.DEAD_CODE),
            # ESLint
            ("no-unused-vars", DeadCodeCategory.UNUSED_VARIABLE),
            ("unused-imports/no-unused-imports", DeadCodeCategory.UNUSED_IMPORT),
            ("@typescript-eslint/no-unused-vars", DeadCodeCategory.UNUSED_VARIABLE),
            # csharp-ls / Roslyn (C#)
            ("CS8019", DeadCodeCategory.UNUSED_IMPORT),
            ("CS0168", DeadCodeCategory.UNUSED_VARIABLE),
            ("CS0219", DeadCodeCategory.UNUSED_VARIABLE),
            ("CS0169", DeadCodeCategory.DEAD_CODE),
            ("CS0414", DeadCodeCategory.DEAD_CODE),
            ("CS0649", DeadCodeCategory.DEAD_CODE),
            ("CS0162", DeadCodeCategory.UNREACHABLE_CODE),
            ("CS8321", DeadCodeCategory.UNUSED_FUNCTION),
            ("IDE0051", DeadCodeCategory.UNUSED_FUNCTION),
            ("IDE0052", DeadCodeCategory.DEAD_CODE),
            ("IDE0059", DeadCodeCategory.UNUSED_VARIABLE),
            ("IDE0060", DeadCodeCategory.UNUSED_PARAMETER),
        ],
    )
    def test_code_maps_to_correct_category(self, code, expected_category):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic(code, "some message", line=5),
        )
        issues = collector.process_diagnostics()
        assert len(issues) == 1
        assert issues[0].category == expected_category

    def test_all_code_mappings_produce_non_unknown_category(self):
        """Every entry in DIAGNOSTIC_CODE_MAPPINGS should yield a recognized category."""
        for code, expected in DIAGNOSTIC_CODE_MAPPINGS.items():
            assert expected != DeadCodeCategory.UNKNOWN or code in (
                "shadow",
                "unusedSymbol",
                "org.eclipse.jdt.core.compiler.problem.unusedPrivateMember",
            ), f"Code {code!r} maps to UNKNOWN unexpectedly"


# ---------------------------------------------------------------------------
# Message-based categorization
# ---------------------------------------------------------------------------


class TestMessageBasedCategorization:
    """Verify fallback categorization via message keywords."""

    @pytest.mark.parametrize(
        "message,expected_category",
        [
            ("'os' is imported but not used", DeadCodeCategory.UNUSED_IMPORT),
            ("unused import 'json'", DeadCodeCategory.UNUSED_IMPORT),
            ("Variable 'x' is declared but never used", DeadCodeCategory.UNUSED_VARIABLE),
            ("'result' is not accessed", DeadCodeCategory.UNUSED_VARIABLE),
            ("'count' is never read", DeadCodeCategory.UNUSED_VARIABLE),
            ("Code is unreachable", DeadCodeCategory.UNREACHABLE_CODE),
            ("dead code detected", DeadCodeCategory.DEAD_CODE),
            ("Statement is never executed", DeadCodeCategory.UNREACHABLE_CODE),
            ("'y' is not used", DeadCodeCategory.UNUSED_VARIABLE),
        ],
    )
    def test_message_keyword_categorization(self, message, expected_category):
        """Use an unrecognized code so the categorizer falls through to message matching."""
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("", message, line=1, tags=[1]),
        )
        issues = collector.process_diagnostics()
        assert len(issues) == 1
        assert issues[0].category == expected_category


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------


class TestSeverityMapping:
    """Verify LSP severity values map to correct Severity enum."""

    def test_error_maps_to_critical(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("reportUnusedImport", "unused", line=0, severity=1),
        )
        issues = collector.process_diagnostics()
        assert issues[0].severity == Severity.CRITICAL

    def test_warning_maps_to_warning(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("reportUnusedImport", "unused", line=0, severity=2),
        )
        issues = collector.process_diagnostics()
        assert issues[0].severity == Severity.WARNING

    def test_info_maps_to_info(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("reportUnusedImport", "unused", line=0, severity=3),
        )
        issues = collector.process_diagnostics()
        assert issues[0].severity == Severity.INFO

    def test_hint_maps_to_info(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("reportUnusedImport", "unused", line=0, severity=4),
        )
        issues = collector.process_diagnostics()
        assert issues[0].severity == Severity.INFO


# ---------------------------------------------------------------------------
# Unnecessary tag fallback
# ---------------------------------------------------------------------------


class TestUnnecessaryTagFallback:
    """When code is unrecognized but tag=1 (Unnecessary), should still categorize."""

    def test_unnecessary_tag_with_import_message(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("", "'os' imported but unused", line=2, tags=[1]),
        )
        issues = collector.process_diagnostics()
        assert len(issues) == 1
        assert issues[0].category == DeadCodeCategory.UNUSED_IMPORT

    def test_unnecessary_tag_defaults_to_unused_variable(self):
        """No matching message keyword, but tag=1 -> default to UNUSED_VARIABLE."""
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic(
            "/f.py",
            _make_diagnostic("", "something flagged", line=3, tags=[1]),
        )
        issues = collector.process_diagnostics()
        assert len(issues) == 1
        assert issues[0].category == DeadCodeCategory.UNUSED_VARIABLE


# ---------------------------------------------------------------------------
# Deduplication edge cases
# ---------------------------------------------------------------------------


class TestDeduplicationEdgeCases:
    """More deduplication scenarios."""

    def test_same_category_same_line_different_files_not_deduplicated(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic("/a.py", _make_diagnostic("reportUnusedImport", "unused", line=5))
        collector.add_diagnostic("/b.py", _make_diagnostic("reportUnusedImport", "unused", line=5))
        issues = collector.process_diagnostics()
        assert len(issues) == 2

    def test_same_file_different_lines_not_deduplicated(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic("/a.py", _make_diagnostic("reportUnusedImport", "unused os", line=1))
        collector.add_diagnostic("/a.py", _make_diagnostic("reportUnusedImport", "unused sys", line=2))
        issues = collector.process_diagnostics()
        assert len(issues) == 2

    def test_different_categories_same_line_not_deduplicated(self):
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic("/a.py", _make_diagnostic("reportUnusedImport", "unused import", line=5))
        collector.add_diagnostic("/a.py", _make_diagnostic("reportUnusedVariable", "unused var", line=5))
        issues = collector.process_diagnostics()
        assert len(issues) == 2


# ---------------------------------------------------------------------------
# check_unused_code_diagnostics finding groups
# ---------------------------------------------------------------------------


class TestFindingGroupsStructure:
    """Verify the structure of finding groups in the check summary."""

    def test_finding_groups_have_correct_line_numbers(self):
        """Entities in finding groups should have correct 1-based line numbers."""
        collector = LSPDiagnosticsCollector()
        collector.add_diagnostic("/a.py", _make_diagnostic("reportUnusedImport", "unused os", line=0))
        collector.add_diagnostic("/a.py", _make_diagnostic("reportUnusedVariable", "unused x", line=9))
        collector.add_diagnostic("/b.py", _make_diagnostic("reportUnusedFunction", "unused fn", line=49))

        summary = check_unused_code_diagnostics(collector)

        all_entities = []
        for group in summary.finding_groups:
            all_entities.extend(group.entities)

        # Collect (file, line_start) pairs
        entity_locs = {(e.file_path, e.line_start) for e in all_entities}
        assert ("/a.py", 1) in entity_locs  # line 0 -> 1
        assert ("/a.py", 10) in entity_locs  # line 9 -> 10
        assert ("/b.py", 50) in entity_locs  # line 49 -> 50
