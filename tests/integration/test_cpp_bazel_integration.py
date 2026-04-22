"""Integration test for the Bazel CDB-generation path.

Exercises the full stack: MODULE.bazel workspace -> bazel aquery ->
BazelAqueryGenerator -> compile_commands.json -> clangd -> StaticAnalyzer.
The test opts in to generation via ``CODEBOARDING_CPP_GENERATE_CDB=1`` and
verifies the pinned fixture in ``fixtures/edge_cases/cpp_bazel_edge_cases.json``.

Skipped cleanly when ``bazel`` is missing or is Bazel 5.x (aquery jsonproto
schema changed in 6.x; the generator version-checks for that).

Usage:
    uv run pytest tests/integration/test_cpp_bazel_integration.py -v
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from static_analyzer import StaticAnalyzer

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent / "projects" / "cpp_bazel_edge_cases_project"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "edge_cases" / "cpp_bazel_edge_cases.json"

pytestmark = [pytest.mark.integration, pytest.mark.cpp_lang]


def _bazel_major_version() -> int | None:
    """Return the Bazel major version, or ``None`` if it can't be probed."""
    try:
        result = subprocess.run(
            ["bazel", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"bazel\s+(\d+)\.", result.stdout + result.stderr, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _cleanup_bazel_artifacts(project_root: Path) -> None:
    """Remove generated CDB and Bazel's per-invocation side effects.

    ``bazel-*`` symlinks point into an ephemeral output base; ``MODULE.bazel.lock``
    is created on first bzlmod resolution. Both are project-local side effects
    of any ``bazel`` invocation and must not leak between test runs or into
    git status.
    """
    cdb_dir = project_root / ".codeboarding"
    if cdb_dir.exists():
        shutil.rmtree(cdb_dir, ignore_errors=True)
    lock = project_root / "MODULE.bazel.lock"
    if lock.exists():
        lock.unlink()
    for entry in project_root.iterdir():
        if entry.name.startswith("bazel-") and entry.is_symlink():
            try:
                entry.unlink()
            except OSError:
                logger.warning("Could not remove %s", entry)


@pytest.fixture
def bazel_project(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Opt into CDB generation and hand back the project path.

    Cleanup unconditionally removes ``.codeboarding/`` and any
    ``bazel-*`` symlinks so a failed run doesn't taint the tree.
    """
    assert PROJECT_DIR.is_dir(), f"Project directory not found: {PROJECT_DIR}"
    monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
    _cleanup_bazel_artifacts(PROJECT_DIR)
    try:
        yield PROJECT_DIR
    finally:
        _cleanup_bazel_artifacts(PROJECT_DIR)


@pytest.mark.skipif(shutil.which("bazel") is None, reason="bazel not installed")
@pytest.mark.skipif(
    (_bazel_major_version() or 0) < 6,
    reason="Bazel < 6 (aquery jsonproto schema incompatible)",
)
def test_bazel_cdb_generation_and_analysis(bazel_project: Path) -> None:
    """Run the analyzer with Bazel auto-CDB enabled; validate the fixture."""
    fixture = json.loads(FIXTURE_PATH.read_text())

    with StaticAnalyzer(bazel_project) as analyzer:
        results = analyzer.analyze(cache_dir=None)

    # The generator should have produced a CDB under .codeboarding/cdb/.
    cdb_path = bazel_project / ".codeboarding" / "cdb" / "compile_commands.json"
    assert cdb_path.is_file(), f"CDB was not generated at {cdb_path}"
    cdb_entries = json.loads(cdb_path.read_text())
    assert cdb_entries, "CDB is empty — bazel aquery returned no CppCompile actions"
    for entry in cdb_entries:
        assert "arguments" in entry, "CDB entry missing 'arguments' (bazel_generator emits arrays, not command strings)"
        assert "file" in entry, "CDB entry missing 'file'"
        assert "directory" in entry, "CDB entry missing 'directory'"

    language = fixture["language"]
    detected = results.get_languages()
    assert language in detected, f"Expected language {language!r} not detected. Found: {detected}"

    # Source files: pin the exact set the analyzer found. Paths are made
    # relative via the resolved project root to match the POSIX-formatted
    # fixture list (same pattern as test_lsp_analysis_for_edge_cases).
    source_files = results.get_source_files(language)
    project_resolved = bazel_project.resolve()
    source_files_rel = {Path(f).relative_to(project_resolved).as_posix() for f in source_files}
    expected_sources = set(fixture.get("expected_source_files", []))
    missing_sources = sorted(expected_sources - source_files_rel)
    unexpected_sources = sorted(source_files_rel - expected_sources)
    errors: list[str] = []
    if missing_sources:
        errors.append("Missing expected source files:\n" + "\n".join(f"  - {f}" for f in missing_sources))
    if unexpected_sources:
        errors.append("Found unexpected source files:\n" + "\n".join(f"  + {f}" for f in unexpected_sources))
    assert not errors, "\n\n".join(errors)

    # References
    refs = results.results[language].get("references", {})
    expected_refs = set(fixture.get("expected_references", []))
    actual_refs = set(refs.keys())
    missing_refs = sorted(expected_refs - actual_refs)
    unexpected_refs = sorted(actual_refs - expected_refs)
    ref_errors: list[str] = []
    if missing_refs:
        ref_errors.append("Missing expected references:\n" + "\n".join(f"  - {r}" for r in missing_refs))
    if unexpected_refs:
        ref_errors.append("Found unexpected references:\n" + "\n".join(f"  + {r}" for r in unexpected_refs))
    assert not ref_errors, "\n\n".join(ref_errors)

    # Call graph edges
    cfg = results.get_cfg(language)
    actual_edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}
    expected_edges = {(s, d) for s, d in fixture.get("expected_edges", [])}
    missing_edges = sorted(f"{s} -> {d}" for s, d in expected_edges - actual_edges)
    unexpected_edges = sorted(f"{s} -> {d}" for s, d in actual_edges - expected_edges)
    edge_errors: list[str] = []
    if missing_edges:
        edge_errors.append("Missing expected edges:\n" + "\n".join(f"  - {e}" for e in missing_edges))
    if unexpected_edges:
        edge_errors.append("Found unexpected edges:\n" + "\n".join(f"  + {e}" for e in unexpected_edges))
    assert not edge_errors, "\n\n".join(edge_errors)

    # Package dependencies
    deps = results.get_package_dependencies(language)
    expected_deps = fixture.get("expected_package_deps", {})
    pkg_errors: list[str] = []
    for pkg_name, expectations in expected_deps.items():
        if pkg_name not in deps:
            pkg_errors.append(f"Package {pkg_name!r} not found in deps (found: {sorted(deps.keys())})")
            continue
        pkg_info = deps[pkg_name]
        for expected_import in expectations.get("imports_contain", []):
            if expected_import not in pkg_info.get("imports", []):
                pkg_errors.append(
                    f"{pkg_name!r} missing import {expected_import!r} (has: {pkg_info.get('imports', [])})"
                )
        for expected_importer in expectations.get("imported_by_contain", []):
            if expected_importer not in pkg_info.get("imported_by", []):
                pkg_errors.append(
                    f"{pkg_name!r} missing imported_by {expected_importer!r} "
                    f"(has: {pkg_info.get('imported_by', [])})"
                )
    unexpected_pkgs = sorted(set(deps.keys()) - set(expected_deps.keys()))
    if unexpected_pkgs:
        pkg_errors.append("Unexpected packages: " + ", ".join(unexpected_pkgs))
    assert not pkg_errors, "\n".join(pkg_errors)
