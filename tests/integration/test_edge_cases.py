"""Integration tests for static analysis using local dummy projects.

Each test runs the real StaticAnalyzer (scanner + LSP) against a small,
purpose-built project that exercises language-specific corner cases.
Results are validated against a fixture JSON that pins expected references,
class hierarchy, call graph edges, package dependencies, and source files.

The fixture describes what a CORRECT analyzer should find — if the LSP
disagrees, the test fails, surfacing a real bug.

Usage:
    # Run all edge-case integration tests
    STATIC_ANALYSIS_CONFIG=static_analysis_config.yml uv run pytest tests/integration/test_edge_cases.py -m integration -v

    # Run only the Python edge-case test
    STATIC_ANALYSIS_CONFIG=static_analysis_config.yml uv run pytest tests/integration/test_edge_cases.py -k python -m integration -v
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from static_analyzer import StaticAnalyzer

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).parent / "projects"
FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Each test runs this many times to verify deterministic output
STABILITY_RUNS = 10


@dataclass(frozen=True)
class EdgeCaseProject:
    """Configuration for a local edge-case project test."""

    name: str
    project_dir: str
    language: str
    fixture_file: str
    stability_runs: int = STABILITY_RUNS


EDGE_CASE_PROJECTS = [
    EdgeCaseProject(
        name="python_edge_cases",
        project_dir="python_edge_cases_project",
        language="Python",
        fixture_file="python_edge_cases.json",
    ),
    EdgeCaseProject(
        name="python_scaled",
        project_dir="python_scaled_project",
        language="Python",
        fixture_file="python_scaled.json",
        stability_runs=2,
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
        name="javascript_scaled",
        project_dir="javascript_scaled_project",
        language="JavaScript",
        fixture_file="javascript_scaled.json",
        stability_runs=2,
    ),
    EdgeCaseProject(
        name="typescript_edge_cases",
        project_dir="typescript_edge_cases_project",
        language="TypeScript",
        fixture_file="typescript_edge_cases.json",
    ),
    EdgeCaseProject(
        name="typescript_scaled",
        project_dir="typescript_scaled_project",
        language="TypeScript",
        fixture_file="typescript_scaled.json",
        stability_runs=2,
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
]


def _load_fixture(filename: str) -> dict:
    with open(FIXTURE_DIR / filename) as f:
        return json.load(f)


def _generate_test_params():
    params = []
    for project in EDGE_CASE_PROJECTS:
        markers = [pytest.mark.integration]
        params.append(pytest.param(project, marks=markers, id=project.name))
    return params


@pytest.mark.integration
class TestEdgeCases:
    """Run real LSP analysis against local dummy projects, validate all outputs."""

    @pytest.mark.parametrize("project", _generate_test_params())
    def test_static_analysis_edge_cases(self, project: EdgeCaseProject):
        """Run analysis STABILITY_RUNS times and validate every run produces identical results."""
        project_path = PROJECTS_DIR / project.project_dir
        assert project_path.is_dir(), f"Project directory not found: {project_path}"

        fixture = _load_fixture(project.fixture_file)
        language = fixture["language"]

        first_run_metrics: dict | None = None

        for run in range(1, project.stability_runs + 1):
            # Run real StaticAnalyzer — no mocks, fresh instance each time
            analyzer = StaticAnalyzer(project_path)
            results = analyzer.analyze(cache_dir=None)

            # --- 1. Verify language detected ---
            detected_languages = results.get_languages()
            assert (
                language in detected_languages
            ), f"[run {run}] Expected language '{language}' not detected. Found: {detected_languages}"

            # --- 2. Verify sample references exist ---
            refs = results.results[language].get("references", {})
            missing_refs = []
            for expected_ref in fixture.get("sample_references", []):
                if expected_ref not in refs:
                    missing_refs.append(expected_ref)
            assert not missing_refs, f"[run {run}] Missing {len(missing_refs)} expected references:\n" + "\n".join(
                f"  - {r}" for r in missing_refs
            )

            # --- 3. Verify sample classes in hierarchy ---
            hierarchy = results.get_hierarchy(language)
            missing_classes = []
            for expected_cls in fixture.get("sample_classes", []):
                if expected_cls not in hierarchy:
                    missing_classes.append(expected_cls)
            assert (
                not missing_classes
            ), f"[run {run}] Missing {len(missing_classes)} expected classes in hierarchy:\n" + "\n".join(
                f"  - {c}" for c in missing_classes
            )

            # --- 4. Verify inheritance relationships ---
            for cls_name, expectations in fixture.get("sample_hierarchy", {}).items():
                assert cls_name in hierarchy, f"[run {run}] Class '{cls_name}' not found in hierarchy"
                cls_info = hierarchy[cls_name]

                for expected_super in expectations.get("superclasses_contain", []):
                    assert expected_super in cls_info.get("superclasses", []), (
                        f"[run {run}] Class '{cls_name}' should have superclass '{expected_super}', "
                        f"but superclasses are: {cls_info.get('superclasses', [])}"
                    )

                for expected_sub in expectations.get("subclasses_contain", []):
                    assert expected_sub in cls_info.get("subclasses", []), (
                        f"[run {run}] Class '{cls_name}' should have subclass '{expected_sub}', "
                        f"but subclasses are: {cls_info.get('subclasses', [])}"
                    )

            # --- 5. Verify call graph edges ---
            cfg = results.get_cfg(language)
            actual_edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}
            missing_edges = []
            for src, dst in fixture.get("sample_edges", []):
                if (src, dst) not in actual_edges:
                    missing_edges.append(f"{src} → {dst}")
            assert not missing_edges, (
                f"[run {run}] Missing {len(missing_edges)} expected call graph edges:\n"
                + "\n".join(f"  - {e}" for e in missing_edges)
                + f"\n\nActual edges ({len(actual_edges)}):\n"
                + "\n".join(f"  - {s} → {d}" for s, d in sorted(actual_edges))
            )

            # --- 6. Verify package dependencies ---
            deps = results.get_package_dependencies(language)
            for pkg_name, expectations in fixture.get("sample_package_deps", {}).items():
                assert pkg_name in deps, (
                    f"[run {run}] Package '{pkg_name}' not found in dependencies. " f"Found: {list(deps.keys())}"
                )
                pkg_info = deps[pkg_name]

                for expected_import in expectations.get("imports_contain", []):
                    assert expected_import in pkg_info.get("imports", []), (
                        f"[run {run}] Package '{pkg_name}' should import '{expected_import}', "
                        f"but imports are: {pkg_info.get('imports', [])}"
                    )

                for expected_importer in expectations.get("imported_by_contain", []):
                    assert expected_importer in pkg_info.get("imported_by", []), (
                        f"[run {run}] Package '{pkg_name}' should be imported by '{expected_importer}', "
                        f"but imported_by is: {pkg_info.get('imported_by', [])}"
                    )

            # --- 7. Verify source files ---
            source_files = results.get_source_files(language)
            source_files_rel = {str(Path(f).relative_to(project_path.resolve())) for f in source_files}
            missing_files = []
            for expected_file in fixture.get("expected_source_files_contain", []):
                if expected_file not in source_files_rel:
                    missing_files.append(expected_file)
            assert not missing_files, (
                f"[run {run}] Missing {len(missing_files)} expected source files:\n"
                + "\n".join(f"  - {f}" for f in missing_files)
                + f"\n\nActual files:\n"
                + "\n".join(f"  - {f}" for f in sorted(source_files_rel))
            )

            # --- 8. Stability check: metrics must be identical across runs ---
            current_metrics = {
                "references": len(refs),
                "classes": len(hierarchy),
                "packages": len(deps),
                "graph_nodes": len(cfg.nodes),
                "graph_edges": len(cfg.edges),
                "source_files": len(source_files),
                "edge_set": sorted((s, d) for s, d in actual_edges),
                "reference_keys": sorted(refs.keys()),
                "hierarchy_keys": sorted(hierarchy.keys()),
            }

            if first_run_metrics is None:
                first_run_metrics = current_metrics
            else:
                for key in first_run_metrics:
                    assert current_metrics[key] == first_run_metrics[key], (
                        f"[run {run}] Stability failure — '{key}' differs from run 1:\n"
                        f"  run 1: {first_run_metrics[key]}\n"
                        f"  run {run}: {current_metrics[key]}"
                    )

            print(
                f"  [run {run}/{project.stability_runs}] PASS — "
                f"refs={len(refs)}, classes={len(hierarchy)}, "
                f"edges={len(cfg.edges)}, nodes={len(cfg.nodes)}"
            )

        # --- Final summary ---
        print(f"\n{'=' * 70}")
        print(
            f"Edge Case Test: {project.name} ({language}) — "
            f"{project.stability_runs}/{project.stability_runs} runs identical"
        )
        print(f"{'=' * 70}")
        assert first_run_metrics is not None
        print(f"  References:    {first_run_metrics['references']}")
        print(f"  Classes:       {first_run_metrics['classes']}")
        print(f"  Packages:      {first_run_metrics['packages']}")
        print(f"  Graph nodes:   {first_run_metrics['graph_nodes']}")
        print(f"  Graph edges:   {first_run_metrics['graph_edges']}")
        print(f"  Source files:  {first_run_metrics['source_files']}")
        print(f"{'=' * 70}")
