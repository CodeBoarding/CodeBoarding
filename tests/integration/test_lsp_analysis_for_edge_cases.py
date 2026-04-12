"""Integration tests for static analysis using local dummy projects.

Each test runs the real StaticAnalyzer (scanner + LSP) against a small,
purpose-built project that exercises language-specific corner cases.
Results are validated against a fixture JSON that pins expected references,
class hierarchy, call graph edges, package dependencies, and source files.

The fixture describes what a CORRECT analyzer should find — if the LSP
disagrees, the test fails, surfacing a real bug.

The analysis is run once per project (via a class-scoped fixture) and
shared across all validation test methods, so each check is reported
independently without re-running the expensive LSP analysis.

Usage:
    # Run all edge-case integration tests
    STATIC_ANALYSIS_CONFIG=static_analysis_config.yml uv run pytest tests/integration/test_lsp_analysis_for_edge_cases.py -m integration -v

    # Run only the Python edge-case test
    STATIC_ANALYSIS_CONFIG=static_analysis_config.yml uv run pytest tests/integration/test_lsp_analysis_for_edge_cases.py -k python -m integration -v
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from static_analyzer import StaticAnalyzer

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).parent / "projects"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "edge_cases"

# Each test runs this many times to verify deterministic output
STABILITY_RUNS = 3


@dataclass(frozen=True)
class EdgeCaseProject:
    """Configuration for a local edge-case project test."""

    name: str
    project_dir: str
    language: str
    fixture_file: str
    stability_runs: int = STABILITY_RUNS


@dataclass
class AnalysisRunData:
    """Holds the analysis results for all stability runs of a single project."""

    project: EdgeCaseProject
    fixture: dict
    all_results: list
    project_path: Path


EDGE_CASE_PROJECTS = [
    EdgeCaseProject(
        name="python_edge_cases",
        project_dir="python_edge_cases_project",
        language="Python",
        fixture_file="python_edge_cases.json",
    ),
    EdgeCaseProject(
        name="javascript_edge_cases",
        project_dir="javascript_edge_cases_project",
        language="JavaScript",
        fixture_file="javascript_edge_cases.json",
    ),
    EdgeCaseProject(
        name="go_edge_cases",
        project_dir="go_edge_cases_project",
        language="Go",
        fixture_file="go_edge_cases.json",
    ),
    EdgeCaseProject(
        name="typescript_edge_cases",
        project_dir="typescript_edge_cases_project",
        language="TypeScript",
        fixture_file="typescript_edge_cases.json",
    ),
    EdgeCaseProject(
        name="java_edge_cases",
        project_dir="java_edge_cases_project",
        language="Java",
        fixture_file="java_edge_cases.json",
    ),
    EdgeCaseProject(
        name="php_edge_cases",
        project_dir="php_edge_cases_project",
        language="PHP",
        fixture_file="php_edge_cases.json",
    ),
    EdgeCaseProject(
        name="rust_edge_cases",
        project_dir="rust_edge_cases_project",
        language="Rust",
        fixture_file="rust_edge_cases.json",
    ),
    EdgeCaseProject(
        name="csharp_edge_cases",
        project_dir="csharp_edge_cases_project",
        language="CSharp",
        fixture_file="csharp_edge_cases.json",
    ),
]


def _load_fixture(filename: str) -> dict:
    with open(FIXTURE_DIR / filename) as f:
        return json.load(f)


_LANGUAGE_MARKERS = {
    "Python": pytest.mark.python_lang,
    "Java": pytest.mark.java_lang,
    "Go": pytest.mark.go_lang,
    "TypeScript": pytest.mark.typescript_lang,
    "PHP": pytest.mark.php_lang,
    "JavaScript": pytest.mark.javascript_lang,
    "Rust": pytest.mark.rust_lang,
    "CSharp": pytest.mark.csharp_lang,
}


def _generate_fixture_params():
    return [
        pytest.param(
            project,
            marks=[pytest.mark.integration, _LANGUAGE_MARKERS[project.language]],
            id=project.name,
        )
        for project in EDGE_CASE_PROJECTS
    ]


@pytest.fixture(scope="class", params=_generate_fixture_params())
def analysis(request) -> AnalysisRunData:
    """Run StaticAnalyzer N times per project; results are shared across all test methods in the class."""
    project: EdgeCaseProject = request.param
    project_path = PROJECTS_DIR / project.project_dir
    assert project_path.is_dir(), f"Project directory not found: {project_path}"

    fixture = _load_fixture(project.fixture_file)

    all_results = []
    for run in range(1, project.stability_runs + 1):
        with StaticAnalyzer(project_path) as analyzer:
            results = analyzer.analyze(cache_dir=None)
        all_results.append(results)
        logger.info(
            "[%s] run %d/%d complete",
            project.name,
            run,
            project.stability_runs,
        )

    return AnalysisRunData(
        project=project,
        fixture=fixture,
        all_results=all_results,
        project_path=project_path,
    )


@pytest.mark.integration
class TestEdgeCases:
    """Run real LSP analysis against local dummy projects, validate all outputs.

    Each validation concern is a separate test method so that pytest reports
    every failure independently.  The expensive analysis is executed once per
    project via the class-scoped ``analysis`` fixture.
    """

    def test_language_detected(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]
        detected = analysis.all_results[0].get_languages()
        assert language in detected, f"Expected language '{language}' not detected. Found: {detected}"

    def test_expected_references(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]
        refs = analysis.all_results[0].results[language].get("references", {})
        expected = set(analysis.fixture.get("expected_references", []))
        actual = set(refs.keys())
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        errors = []
        if missing:
            errors.append(f"Missing {len(missing)} expected references:\n" + "\n".join(f"  - {r}" for r in missing))
        if unexpected:
            errors.append(
                f"Found {len(unexpected)} unexpected references:\n" + "\n".join(f"  + {r}" for r in unexpected)
            )
        assert not errors, "\n\n".join(errors)

    @pytest.mark.skip(reason="Class hierarchy is currently skipped (skip_hierarchy=True in CallGraphBuilder)")
    def test_expected_classes_in_hierarchy(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]
        hierarchy = analysis.all_results[0].get_hierarchy(language)
        expected = set(analysis.fixture.get("expected_classes", []))
        actual = set(hierarchy.keys())
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        errors = []
        if missing:
            errors.append(f"Missing {len(missing)} expected classes:\n" + "\n".join(f"  - {c}" for c in missing))
        if unexpected:
            errors.append(f"Found {len(unexpected)} unexpected classes:\n" + "\n".join(f"  + {c}" for c in unexpected))
        assert not errors, "\n\n".join(errors)

    @pytest.mark.skip(reason="Class hierarchy is currently skipped (skip_hierarchy=True in CallGraphBuilder)")
    def test_inheritance_relationships(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]
        hierarchy = analysis.all_results[0].get_hierarchy(language)
        errors = []
        for cls_name, expectations in analysis.fixture.get("expected_hierarchy", {}).items():
            if cls_name not in hierarchy:
                errors.append(f"Class '{cls_name}' not found in hierarchy")
                continue
            cls_info = hierarchy[cls_name]
            for expected_super in expectations.get("superclasses_contain", []):
                if expected_super not in cls_info.get("superclasses", []):
                    errors.append(
                        f"'{cls_name}' missing superclass '{expected_super}' "
                        f"(has: {cls_info.get('superclasses', [])})"
                    )
            for expected_sub in expectations.get("subclasses_contain", []):
                if expected_sub not in cls_info.get("subclasses", []):
                    errors.append(
                        f"'{cls_name}' missing subclass '{expected_sub}' " f"(has: {cls_info.get('subclasses', [])})"
                    )
        assert not errors, (
            f"{len(errors)} inheritance issues:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + f"\n\nActual hierarchy ({len(hierarchy)}):\n"
            + "\n".join(
                f"  - {c}: supers={info.get('superclasses', [])}, subs={info.get('subclasses', [])}"
                for c, info in sorted(hierarchy.items())
            )
        )

    def test_call_graph_edges(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]
        cfg = analysis.all_results[0].get_cfg(language)
        actual_edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}
        expected_edges = {(s, d) for s, d in analysis.fixture.get("expected_edges", [])}
        missing = sorted(f"{s} -> {d}" for s, d in expected_edges - actual_edges)
        unexpected = sorted(f"{s} -> {d}" for s, d in actual_edges - expected_edges)
        errors = []
        if missing:
            errors.append(f"Missing {len(missing)} expected edges:\n" + "\n".join(f"  - {e}" for e in missing))
        if unexpected:
            errors.append(f"Found {len(unexpected)} unexpected edges:\n" + "\n".join(f"  + {e}" for e in unexpected))
        assert not errors, "\n\n".join(errors)

    def test_package_dependencies(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]
        deps = analysis.all_results[0].get_package_dependencies(language)
        expected_deps = analysis.fixture.get("expected_package_deps", {})
        errors = []
        # Check expected packages exist with correct imports/imported_by
        for pkg_name, expectations in expected_deps.items():
            if pkg_name not in deps:
                errors.append(f"Package '{pkg_name}' not found in dependencies (found: {list(deps.keys())})")
                continue
            pkg_info = deps[pkg_name]
            for expected_import in expectations.get("imports_contain", []):
                if expected_import not in pkg_info.get("imports", []):
                    errors.append(
                        f"'{pkg_name}' missing import '{expected_import}' (has: {pkg_info.get('imports', [])})"
                    )
            for expected_importer in expectations.get("imported_by_contain", []):
                if expected_importer not in pkg_info.get("imported_by", []):
                    errors.append(
                        f"'{pkg_name}' missing imported_by '{expected_importer}' "
                        f"(has: {pkg_info.get('imported_by', [])})"
                    )
        # Check for unexpected packages
        unexpected_pkgs = sorted(set(deps.keys()) - set(expected_deps.keys()))
        if unexpected_pkgs:
            errors.append(
                f"Found {len(unexpected_pkgs)} unexpected packages:\n" + "\n".join(f"  + {p}" for p in unexpected_pkgs)
            )
        assert not errors, (
            f"{len(errors)} package dependency issues:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + f"\n\nActual packages ({len(deps)}):\n"
            + "\n".join(
                f"  - {p}: imports={info.get('imports', [])}, imported_by={info.get('imported_by', [])}"
                for p, info in sorted(deps.items())
            )
        )

    def test_source_files(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]
        source_files = analysis.all_results[0].get_source_files(language)
        # as_posix() keeps the fixture comparison platform-independent —
        # str() on a WindowsPath emits backslashes that don't match the
        # POSIX-formatted expected_source_files list.
        source_files_rel = {Path(f).relative_to(analysis.project_path.resolve()).as_posix() for f in source_files}
        expected = set(analysis.fixture.get("expected_source_files", []))
        missing = sorted(expected - source_files_rel)
        unexpected = sorted(source_files_rel - expected)
        errors = []
        if missing:
            errors.append(f"Missing {len(missing)} expected source files:\n" + "\n".join(f"  - {f}" for f in missing))
        if unexpected:
            errors.append(
                f"Found {len(unexpected)} unexpected source files:\n" + "\n".join(f"  + {f}" for f in unexpected)
            )
        assert not errors, "\n\n".join(errors)

    def test_stability_across_runs(self, analysis: AnalysisRunData):
        language = analysis.fixture["language"]

        def _compute_metrics(results):
            refs = results.results[language].get("references", {})
            deps = results.get_package_dependencies(language)
            cfg = results.get_cfg(language)
            source_files = results.get_source_files(language)
            actual_edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}
            return {
                "references": len(refs),
                "packages": len(deps),
                "graph_nodes": len(cfg.nodes),
                "graph_edges": len(cfg.edges),
                "source_files": len(source_files),
                "edge_set": sorted((s, d) for s, d in actual_edges),
                "reference_keys": sorted(refs.keys()),
            }

        first_metrics = _compute_metrics(analysis.all_results[0])
        errors = []
        for i, results in enumerate(analysis.all_results[1:], start=2):
            metrics = _compute_metrics(results)
            for key in first_metrics:
                if metrics[key] != first_metrics[key]:
                    errors.append(
                        f"[run {i}] '{key}' differs from run 1:\n"
                        f"    run 1: {first_metrics[key]}\n"
                        f"    run {i}: {metrics[key]}"
                    )
        assert not errors, f"Stability failures across {len(analysis.all_results)} runs:\n" + "\n".join(
            f"  - {e}" for e in errors
        )
