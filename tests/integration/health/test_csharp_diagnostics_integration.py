"""End-to-end test that csharp-ls diagnostics flow through to the unused-code health check.

Why: csharp-ls publishes ``textDocument/publishDiagnostics`` asynchronously
after didOpen, so without the diagnostics-quiesce wait the analyzer's
``collected_diagnostics`` would be empty and the unused-code check would
silently report nothing for C#. This test guards against three regressions
at once:
  1. ``CSharpAdapter.diagnostics_quiesce_seconds`` returns a non-zero window.
  2. ``CSharpAdapter.prepare_project`` runs ``dotnet restore`` so csharp-ls
     can resolve framework references (otherwise it floods CS0518).
  3. CS-prefixed codes (CS8019, CS0162, CS0219, ...) are mapped to dead-code
     categories in ``DIAGNOSTIC_CODE_MAPPINGS``.
"""

from pathlib import Path

import pytest

from health.checks.unused_code_diagnostics import (
    DeadCodeCategory,
    LSPDiagnosticsCollector,
    check_unused_code_diagnostics,
)
from health.models import HealthCheckConfig
from static_analyzer import StaticAnalyzer

PROJECT_DIR = Path(__file__).parent.parent / "projects" / "csharp_unused_code_project"


@pytest.mark.integration
@pytest.mark.csharp_lang
class TestCSharpDiagnosticsEndToEnd:
    """Run the analyzer on a project with intentional dead code; assert the
    health check categorizes csharp-ls diagnostics correctly."""

    def test_unused_code_diagnostics_categorizes_csharp_codes(self):
        assert PROJECT_DIR.is_dir(), f"Project missing: {PROJECT_DIR}"

        with StaticAnalyzer(PROJECT_DIR) as analyzer:
            analyzer.analyze(cache_dir=None, skip_cache=True)
            diagnostics_by_file = analyzer.collected_diagnostics.get("CSharp", {})

        assert diagnostics_by_file, (
            "csharp-ls produced no diagnostics — the diagnostics-quiesce wait "
            "is broken or csharp-ls failed to load the project (check for "
            "CS0518 floods which mean dotnet restore didn't run)."
        )

        # Sanity: diagnostics should be real compiler messages, not the
        # CS0518 'Predefined type System.X' flood that means restore was
        # skipped or failed.
        all_codes: set[str] = set()
        for items in diagnostics_by_file.values():
            for d in items:
                if d.code:
                    all_codes.add(d.code)
        assert "CS0518" not in all_codes, (
            "csharp-ls is reporting CS0518 (Predefined type missing) — this "
            "means dotnet restore didn't populate obj/project.assets.json. "
            "Check CSharpAdapter.prepare_project."
        )

        # Feed diagnostics to the unused-code health check.
        collector = LSPDiagnosticsCollector()
        for file_path, items in diagnostics_by_file.items():
            for d in items:
                collector.add_diagnostic(file_path, d)
        issues = collector.process_diagnostics()

        categories = {issue.category for issue in issues}
        assert DeadCodeCategory.UNUSED_VARIABLE in categories, (
            "Expected CS0219 (unused local) to be categorized as UNUSED_VARIABLE. "
            f"Got categories: {categories}, codes seen: {all_codes}"
        )
        assert DeadCodeCategory.UNUSED_IMPORT in categories, (
            "Expected CS8019 (unnecessary using) to be categorized as UNUSED_IMPORT. "
            f"Got categories: {categories}, codes seen: {all_codes}"
        )
        assert DeadCodeCategory.UNREACHABLE_CODE in categories, (
            "Expected CS0162 (unreachable code) to be categorized as UNREACHABLE_CODE. "
            f"Got categories: {categories}, codes seen: {all_codes}"
        )

        # And the high-level health check wraps these into findings.
        result = check_unused_code_diagnostics(collector, HealthCheckConfig())
        assert result.findings_count > 0, "Health check should report findings for the dead-code project"
