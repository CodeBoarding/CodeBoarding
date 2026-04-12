"""End-to-end test that rust-analyzer diagnostics flow through to the unused-code health check.

Why: rust-analyzer publishes ``textDocument/publishDiagnostics`` asynchronously
after didOpen, and only emits ``unused_imports`` / ``unused_variables`` /
``dead_code`` codes when ``checkOnSave`` is enabled. Without both pieces in
place the analyzer's ``collected_diagnostics`` would be empty for Rust and
the unused-code health check would silently report nothing. This test guards
against three regressions at once:
  1. ``RustAdapter.diagnostics_quiesce_seconds`` returns a non-zero window.
  2. ``RustAdapter.get_lsp_init_options`` keeps ``checkOnSave`` enabled.
  3. rust-analyzer codes (``unused_imports``, ``unused_variables``,
     ``dead_code``) are mapped to dead-code categories in
     ``DIAGNOSTIC_CODE_MAPPINGS``.
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

PROJECT_DIR = Path(__file__).parent.parent / "projects" / "rust_unused_code_project"


@pytest.mark.integration
@pytest.mark.rust_lang
class TestRustDiagnosticsEndToEnd:
    """Run the analyzer on a Rust project with intentional dead code; assert
    the health check categorizes rust-analyzer diagnostics correctly."""

    def test_unused_code_diagnostics_categorizes_rust_codes(self):
        assert PROJECT_DIR.is_dir(), f"Project missing: {PROJECT_DIR}"

        with StaticAnalyzer(PROJECT_DIR) as analyzer:
            analyzer.analyze(cache_dir=None, skip_cache=True)
            diagnostics_by_file = analyzer.collected_diagnostics.get("Rust", {})

        assert diagnostics_by_file, (
            "rust-analyzer produced no diagnostics — the diagnostics-quiesce "
            "wait is broken or checkOnSave is disabled (cargo check is what "
            "surfaces unused_imports/unused_variables/dead_code)."
        )

        all_codes: set[str] = set()
        for items in diagnostics_by_file.values():
            for d in items:
                if d.code:
                    all_codes.add(d.code)

        # Feed diagnostics to the unused-code health check.
        collector = LSPDiagnosticsCollector()
        for file_path, items in diagnostics_by_file.items():
            for d in items:
                collector.add_diagnostic(file_path, d)
        issues = collector.process_diagnostics()
        categories = {issue.category for issue in issues}

        assert DeadCodeCategory.UNUSED_IMPORT in categories, (
            "Expected `unused_imports` to be categorized as UNUSED_IMPORT. "
            f"Got categories: {categories}, codes seen: {all_codes}"
        )
        assert DeadCodeCategory.UNUSED_VARIABLE in categories, (
            "Expected `unused_variables` to be categorized as UNUSED_VARIABLE. "
            f"Got categories: {categories}, codes seen: {all_codes}"
        )
        assert DeadCodeCategory.DEAD_CODE in categories, (
            "Expected `dead_code` to be categorized as DEAD_CODE. "
            f"Got categories: {categories}, codes seen: {all_codes}"
        )

        result = check_unused_code_diagnostics(collector, HealthCheckConfig())
        assert result.findings_count > 0, "Health check should report findings for the dead-code project"
