"""End-to-end integration test for the Make + Bear CDB-generation path.

Proves that with ``CODEBOARDING_CPP_GENERATE_CDB=1`` and only a bare
Makefile on disk, ``CppAdapter.prepare_project`` drives
``bear -- make clean all``, writes ``compile_commands.json`` under
``.codeboarding/cdb/``, and clangd analyzes the result correctly —
exercising the full chain (Make -> Bear -> clangd -> analyzer).

Runs only when ``bear`` and ``make`` are on PATH; skipped otherwise so
CI hosts without Bear don't false-fail.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest

from static_analyzer import StaticAnalyzer

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent / "projects" / "cpp_make_edge_cases_project"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "edge_cases" / "cpp_make_edge_cases.json"

pytestmark = [pytest.mark.integration, pytest.mark.cpp_lang]


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def _cleanup_generated(project_root: Path) -> None:
    """Remove anything Bear or Make left behind so repeat runs start clean."""
    shutil.rmtree(project_root / ".codeboarding", ignore_errors=True)
    for obj in (project_root / "src").glob("*.o"):
        obj.unlink(missing_ok=True)
    (project_root / "demo").unlink(missing_ok=True)


@pytest.mark.skipif(
    shutil.which("bear") is None or shutil.which("make") is None,
    reason="bear/make not installed",
)
def test_make_bear_cdb_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full stack: Make -> Bear -> clangd -> StaticAnalyzer, validated against fixture."""
    assert PROJECT_DIR.is_dir(), f"Project directory not found: {PROJECT_DIR}"
    fixture = _load_fixture()
    language = fixture["language"]

    monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
    _cleanup_generated(PROJECT_DIR)

    try:
        with StaticAnalyzer(PROJECT_DIR) as analyzer:
            results = analyzer.analyze(cache_dir=None)

        # Bear must have produced the CDB under .codeboarding/cdb/ —
        # this is the whole point of the test.
        cdb_path = PROJECT_DIR / ".codeboarding" / "cdb" / "compile_commands.json"
        assert cdb_path.is_file(), f"Bear did not produce {cdb_path}"

        # Language detection
        detected = results.get_languages()
        assert language in detected, f"Expected '{language}' not detected. Found: {detected}"

        # Source files (POSIX paths so the fixture stays platform-portable)
        source_files = results.get_source_files(language)
        source_files_rel = {Path(f).relative_to(PROJECT_DIR.resolve()).as_posix() for f in source_files}
        expected_sources = set(fixture["expected_source_files"])
        assert source_files_rel == expected_sources, (
            f"Source files mismatch.\n"
            f"  missing: {sorted(expected_sources - source_files_rel)}\n"
            f"  unexpected: {sorted(source_files_rel - expected_sources)}"
        )

        # References: exact set match (the tiny project has no room for
        # std-library leakage in the empirically captured fixture).
        refs = results.results[language].get("references", {})
        expected_refs = set(fixture["expected_references"])
        actual_refs = set(refs.keys())
        assert actual_refs == expected_refs, (
            f"References mismatch.\n"
            f"  missing: {sorted(expected_refs - actual_refs)}\n"
            f"  unexpected: {sorted(actual_refs - expected_refs)}"
        )

        # Call-graph edges: exact set match.
        cfg = results.get_cfg(language)
        actual_edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}
        expected_edges = {(s, d) for s, d in fixture["expected_edges"]}
        assert actual_edges == expected_edges, (
            f"Edges mismatch.\n"
            f"  missing: {sorted(f'{s} -> {d}' for s, d in expected_edges - actual_edges)}\n"
            f"  unexpected: {sorted(f'{s} -> {d}' for s, d in actual_edges - expected_edges)}"
        )

        # Package deps: require the expected packages exist with the expected
        # imports/imported_by, and disallow unexpected packages.
        deps = results.get_package_dependencies(language)
        expected_deps = fixture["expected_package_deps"]
        assert set(deps.keys()) == set(
            expected_deps.keys()
        ), f"Package set mismatch. expected={sorted(expected_deps.keys())}, actual={sorted(deps.keys())}"
        for pkg_name, expectations in expected_deps.items():
            for expected_import in expectations.get("imports_contain", []):
                assert expected_import in deps[pkg_name].get("imports", []), (
                    f"'{pkg_name}' missing import '{expected_import}' " f"(has: {deps[pkg_name].get('imports', [])})"
                )
            for expected_importer in expectations.get("imported_by_contain", []):
                assert expected_importer in deps[pkg_name].get("imported_by", []), (
                    f"'{pkg_name}' missing imported_by '{expected_importer}' "
                    f"(has: {deps[pkg_name].get('imported_by', [])})"
                )
    finally:
        _cleanup_generated(PROJECT_DIR)
