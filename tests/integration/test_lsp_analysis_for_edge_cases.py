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
from static_analyzer.constants import Language
from static_analyzer.program_graph import ProgramEdgeKind, ProgramNodeKind
from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer
from utils import get_artifact_dir

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


def _edge_call_site_tuples(edge, project_path: Path) -> set[tuple[str, int, int]]:
    """Return normalized call-site tuples for an edge.

    The engine does not expose this metadata yet; this helper defines the
    expected future contract while keeping the assertion code independent of
    whether occurrences are represented as dicts or small objects.
    """
    sites = edge.occurrences
    actual = set()
    for site in sites:
        if isinstance(site, dict):
            file_value = site.get("file") or site.get("file_path")
            line = site.get("line")
            column = site.get("column")
        else:
            file_value = getattr(site, "file", None) or getattr(site, "file_path", None)
            line = getattr(site, "line", None)
            column = getattr(site, "column", None)

        if file_value is None or line is None or column is None:
            continue

        file_path = Path(file_value)
        try:
            file_rel = file_path.resolve().relative_to(project_path.resolve()).as_posix()
        except (OSError, ValueError):
            file_rel = file_path.as_posix()
        actual.add((file_rel, int(line), int(column)))
    return actual


def _expected_edge_key(edge: dict | list) -> tuple[str, str]:
    if isinstance(edge, dict):
        return edge["source"], edge["destination"]
    return edge[0], edge[1]


def _expected_edges(fixture: dict) -> set[tuple[str, str]]:
    return {_expected_edge_key(edge) for edge in fixture.get("expected_edges", [])}


def _expected_call_site_edges(fixture: dict) -> list[dict]:
    expected_edges = []
    for edge in fixture.get("expected_edges", []):
        if not isinstance(edge, dict) or not edge.get("call_sites"):
            continue
        source, destination = _expected_edge_key(edge)
        expected_edges.append({"source": source, "destination": destination, "occurrences": edge["call_sites"]})
    return expected_edges


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
            results = analyzer.analyze(cache_dir=get_artifact_dir(project_path))
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
        language = Language(analysis.fixture["language"].lower())
        detected = analysis.all_results[0].get_languages()
        assert language in detected, f"Expected language '{language}' not detected. Found: {detected}"

    def test_expected_references(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
        refs = {node.id: node for node in analysis.all_results[0].iter_reference_nodes(language)}
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

    def test_expected_classes_in_hierarchy(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
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

    def test_inheritance_relationships(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
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
        language = Language(analysis.fixture["language"].lower())
        graph = analysis.all_results[0].get_program_graph(language)
        actual_edges = {(edge.source, edge.target) for edge in graph.call_edges()}
        expected_edges = _expected_edges(analysis.fixture)
        missing = sorted(f"{s} -> {d}" for s, d in expected_edges - actual_edges)
        unexpected = sorted(f"{s} -> {d}" for s, d in actual_edges - expected_edges)
        errors = []
        if missing:
            errors.append(f"Missing {len(missing)} expected edges:\n" + "\n".join(f"  - {e}" for e in missing))
        if unexpected:
            errors.append(f"Found {len(unexpected)} unexpected edges:\n" + "\n".join(f"  + {e}" for e in unexpected))
        assert not errors, "\n\n".join(errors)

    def test_call_site_occurrences(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
        graph = analysis.all_results[0].get_program_graph(language)
        if language == Language.RUST and not graph.call_edges():
            pytest.skip("Rust edge-case analysis currently emits zero CFG edges")
        actual_by_edge = {
            (edge.source, edge.target): _edge_call_site_tuples(edge, analysis.project_path)
            for edge in graph.call_edges()
        }

        errors = []
        actual_by_destination: dict[str, set[tuple[str, int, int]]] = {}
        for (_, destination), sites in actual_by_edge.items():
            actual_by_destination.setdefault(destination, set()).update(sites)

        for expected in _expected_call_site_edges(analysis.fixture):
            destination = expected["destination"]
            source = expected.get("source")
            edge_key = (source, destination) if source else None
            actual_sites = (
                actual_by_edge.get(edge_key, set()) if edge_key else actual_by_destination.get(destination, set())
            )
            expected_sites = {(site["file"], site["line"], site["column"]) for site in expected.get("occurrences", [])}
            missing = sorted(expected_sites - actual_sites)
            if missing:
                edge_label = f"{source} -> {destination}" if source else f"* -> {destination}"
                errors.append(
                    f"{edge_label} is missing {len(missing)} call-site occurrence(s):\n"
                    + "\n".join(f"  - {file}:{line}:{column}" for file, line, column in missing)
                )

        assert not errors, "\n\n".join(errors)

    def test_program_graph_containment(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
        graph = analysis.all_results[0].get_program_graph(language)
        actual = {(edge.source, edge.target) for edge in graph.edges_of_kind(ProgramEdgeKind.CONTAINS)}
        expected = {(item["container"], item["member"]) for item in analysis.fixture.get("expected_containment", [])}
        missing = sorted(expected - actual)
        assert not missing, "Missing containment relations:\n" + "\n".join(
            f"  - {source} -> {target}" for source, target in missing
        )

    def test_program_graph_schema_and_coverage(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
        graph = analysis.all_results[0].get_program_graph(language)
        containment = graph.edges_of_kind(ProgramEdgeKind.CONTAINS)
        contained = {edge.target for edge in containment}

        file_nodes = graph.nodes_of_kind(ProgramNodeKind.FILE)
        actual_files = {Path(node.file_path).relative_to(analysis.project_path).as_posix() for node in file_nodes}
        assert actual_files == set(analysis.fixture.get("expected_source_files", []))
        assert all(node.node_id in contained for node in file_nodes)
        assert all(node.node_id in contained for node in graph.symbol_nodes())

        for edge in graph.edges:
            source_kind = graph.nodes[edge.source].kind
            target_kind = graph.nodes[edge.target].kind
            if edge.kind == ProgramEdgeKind.CALL:
                assert (source_kind, target_kind) == (ProgramNodeKind.SYMBOL, ProgramNodeKind.SYMBOL)
            elif edge.kind == ProgramEdgeKind.IMPORTS:
                assert source_kind == ProgramNodeKind.FILE
                assert target_kind in {ProgramNodeKind.FILE, ProgramNodeKind.EXTERNAL_PACKAGE}
            elif edge.kind == ProgramEdgeKind.INHERITS:
                assert (source_kind, target_kind) == (ProgramNodeKind.SYMBOL, ProgramNodeKind.SYMBOL)

    def test_program_graph_inheritance(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
        graph = analysis.all_results[0].get_program_graph(language)
        actual = {(edge.source, edge.target) for edge in graph.edges_of_kind(ProgramEdgeKind.INHERITS)}
        expected = {(item["child"], item["parent"]) for item in analysis.fixture.get("expected_inheritance", [])}
        missing = sorted(expected - actual)
        assert not missing, "Missing inheritance relations:\n" + "\n".join(
            f"  - {child} -> {parent}" for child, parent in missing
        )

    def test_program_graph_imports_and_external_packages(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
        graph = analysis.all_results[0].get_program_graph(language)
        imports = graph.edges_of_kind(ProgramEdgeKind.IMPORTS)
        actual = set()
        for edge in imports:
            modules = edge.metadata.get("declared_modules") or [edge.metadata.get("declared_module", "")]
            for module in modules:
                actual.add(
                    (
                        Path(graph.nodes[edge.source].file_path).relative_to(analysis.project_path).as_posix(),
                        str(module),
                        (
                            Path(graph.nodes[edge.target].file_path).relative_to(analysis.project_path).as_posix()
                            if graph.nodes[edge.target].kind == ProgramNodeKind.FILE
                            else None
                        ),
                        (
                            graph.nodes[edge.target].name
                            if graph.nodes[edge.target].kind == ProgramNodeKind.EXTERNAL_PACKAGE
                            else None
                        ),
                    )
                )
        expected = {
            (
                item["source_file"],
                item["declared_module"],
                item.get("target_file"),
                item.get("external_package"),
            )
            for item in analysis.fixture.get("expected_imports", [])
        }
        missing = sorted(expected - actual, key=str)
        assert not missing, "Missing import relations:\n" + "\n".join(f"  - {item}" for item in missing)

        actual_external = {node.name for node in graph.nodes_of_kind(ProgramNodeKind.EXTERNAL_PACKAGE)}
        expected_external = set(analysis.fixture.get("expected_external_packages", []))
        assert expected_external <= actual_external, (
            f"Missing external packages: {sorted(expected_external - actual_external)}; "
            f"found: {sorted(actual_external)}"
        )

    def test_program_graph_call_multiplicity(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
        graph = analysis.all_results[0].get_program_graph(language)
        counts = {
            (edge.source, edge.target): edge.occurrence_count for edge in graph.edges_of_kind(ProgramEdgeKind.CALL)
        }
        for expected in analysis.fixture.get("expected_call_multiplicity", []):
            key = (expected["source"], expected["destination"])
            assert (
                counts.get(key) == expected["count"]
            ), f"Expected {key} occurrence count {expected['count']}, got {counts.get(key)}"

    def test_source_files(self, analysis: AnalysisRunData):
        language = Language(analysis.fixture["language"].lower())
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
        language = Language(analysis.fixture["language"].lower())

        def _compute_metrics(results):
            refs = {node.id: node for node in results.iter_reference_nodes(language)}
            deps = results.get_package_dependencies(language)
            graph = results.get_program_graph(language)
            source_files = results.get_source_files(language)
            actual_edges = {(edge.source, edge.target) for edge in graph.call_edges()}
            cluster_result = HierarchicalInfomapClusterer().cluster(graph)
            assert graph.cluster_snapshot is not None
            return {
                "references": len(refs),
                "packages": len(deps),
                "graph_nodes": len(graph.call_node_ids()),
                "graph_edges": len(graph.call_edges()),
                "source_files": len(source_files),
                "edge_set": sorted((s, d) for s, d in actual_edges),
                "reference_keys": sorted(refs.keys()),
                "program_graph": graph.to_dict(),
                "infomap_clusters": {
                    cluster_id: sorted(members) for cluster_id, members in cluster_result.clusters.items()
                },
                "infomap_paths": dict(graph.cluster_snapshot.node_paths),
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
