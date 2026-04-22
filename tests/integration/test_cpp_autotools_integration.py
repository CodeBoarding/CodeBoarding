"""End-to-end test for the Autotools CDB-generation path.

Exercises ``BearGenerator._run_autotools`` (autoreconf -> configure out-of-tree
-> bear -- make clean all) against a pristine minimal Autotools project, then
feeds the resulting ``compile_commands.json`` to clangd via ``StaticAnalyzer``
and validates the analysis output against a pinned fixture.

Why: the existing ``test_lsp_analysis_for_edge_cases.py`` suite uses a
pre-generated ``compile_flags.txt`` for C++, so the Bear/Autotools path is
never exercised by integration tests. This module covers it.

Usage:
    CODEBOARDING_CPP_GENERATE_CDB=1 STATIC_ANALYSIS_CONFIG=static_analysis_config.yml \\
        uv run pytest tests/integration/test_cpp_autotools_integration.py -m integration -v
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from static_analyzer import StaticAnalyzer

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent / "projects" / "cpp_autotools_edge_cases_project"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "edge_cases" / "cpp_autotools_edge_cases.json"

# Everything autoreconf/automake/configure may drop at the project root.
# Used both as the cleanup list and (by design) as the project's .gitignore —
# keep the two in sync or pristine-state guarantees break.
_AUTORECONF_LEFTOVERS = (
    "configure",
    "Makefile",
    "Makefile.in",
    "aclocal.m4",
    "autom4te.cache",
    "config.log",
    "config.status",
    "depcomp",
    "install-sh",
    "missing",
    "compile",
    "stamp-h1",
)

_REQUIRED_TOOLS = ("bear", "make", "autoreconf", "automake", "autoconf")
_MISSING_TOOLS = [t for t in _REQUIRED_TOOLS if shutil.which(t) is None]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.cpp_lang,
    pytest.mark.skipif(
        bool(_MISSING_TOOLS),
        reason=f"Autotools toolchain incomplete; missing: {', '.join(_MISSING_TOOLS)}",
    ),
]


def _clean_project_dir() -> None:
    """Delete autoreconf/bear leftovers so the next run starts pristine."""
    for name in _AUTORECONF_LEFTOVERS:
        target = PROJECT_DIR / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
    cdb_root = PROJECT_DIR / ".codeboarding"
    if cdb_root.is_dir():
        shutil.rmtree(cdb_root, ignore_errors=True)


@pytest.fixture
def pristine_project(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Yield a pristine project dir + opt-in env; always clean up after.

    Why pristine before: a stale ``./configure`` from a prior run would let
    BearGenerator skip autoreconf, silently degrading test coverage.
    """
    assert PROJECT_DIR.is_dir(), f"Project directory not found: {PROJECT_DIR}"
    assert not (PROJECT_DIR / "configure").exists(), (
        f"Pre-existing ./configure under {PROJECT_DIR}; the project dir must be "
        "checked in pristine. Remove it and re-run."
    )
    _clean_project_dir()
    monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
    try:
        yield PROJECT_DIR
    finally:
        _clean_project_dir()


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def test_autotools_cdb_generation_and_analysis(pristine_project: Path) -> None:
    """Full stack: autoreconf -> configure -> bear -> make -> clangd -> analyzer.

    One test, many asserts: a failure anywhere in the pipeline — generation,
    CDB placement, clangd indexing, or the analyzer's output shape — blocks
    the whole path, so reporting each concern independently buys little.
    """
    fixture = _load_fixture()
    language = fixture["language"]

    with StaticAnalyzer(pristine_project) as analyzer:
        results = analyzer.analyze(cache_dir=None)

    # The generator dropped the CDB where CppAdapter expects it.
    cdb_path = pristine_project / ".codeboarding" / "cdb" / "compile_commands.json"
    assert cdb_path.is_file(), f"Bear did not produce {cdb_path}"
    build_dir = pristine_project / ".codeboarding" / "cdb" / "_build"
    assert build_dir.is_dir(), f"Out-of-tree configure dir missing: {build_dir}"

    # Clangd saw C++ and produced symbols.
    detected = results.get_languages()
    assert language in detected, f"Expected '{language}' in detected languages {detected}"

    refs = results.results[language].get("references", {})
    expected_refs = set(fixture["expected_references"])
    actual_refs = set(refs.keys())
    assert actual_refs == expected_refs, (
        f"References mismatch.\n  missing: {sorted(expected_refs - actual_refs)}\n"
        f"  unexpected: {sorted(actual_refs - expected_refs)}"
    )

    cfg = results.get_cfg(language)
    actual_edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}
    expected_edges = {(s, d) for s, d in fixture["expected_edges"]}
    assert actual_edges == expected_edges, (
        f"Edge set mismatch.\n  missing: {sorted(expected_edges - actual_edges)}\n"
        f"  unexpected: {sorted(actual_edges - expected_edges)}"
    )

    deps = results.get_package_dependencies(language)
    for pkg, expectations in fixture["expected_package_deps"].items():
        assert pkg in deps, f"Package '{pkg}' missing from deps {list(deps.keys())}"
        for imp in expectations["imports_contain"]:
            assert imp in deps[pkg]["imports"], f"'{pkg}' missing import '{imp}' (has {deps[pkg]['imports']})"
        for imp_by in expectations["imported_by_contain"]:
            assert (
                imp_by in deps[pkg]["imported_by"]
            ), f"'{pkg}' missing imported_by '{imp_by}' (has {deps[pkg]['imported_by']})"

    # Source-file list must be project-relative paths, not scratch-build dir
    # paths. Autotools drops config.h / Makefile etc. under .codeboarding/cdb/_build;
    # those must NOT leak into the analyzer's source set.
    source_files = results.get_source_files(language)
    source_rel = {Path(f).relative_to(pristine_project.resolve()).as_posix() for f in source_files}
    expected_sources = set(fixture["expected_source_files"])
    assert source_rel == expected_sources, (
        f"Source file set mismatch.\n  missing: {sorted(expected_sources - source_rel)}\n"
        f"  unexpected: {sorted(source_rel - expected_sources)}"
    )
    for rel in source_rel:
        assert ".codeboarding" not in rel, (
            f"Source file {rel} comes from the scratch build dir; "
            "the analyzer is treating generated files as project sources."
        )
