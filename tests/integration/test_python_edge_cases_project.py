"""Integration test for a local Python project containing parsing edge-cases."""

import json
from pathlib import Path

import pytest

from static_analyzer import StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.scanner import ProjectScanner

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "python_edge_cases_project.json"
PROJECT_PATH = Path(__file__).parent / "projects" / "python_edge_cases_project"


def _extract_metrics(results: StaticAnalysisResults, analyzer: StaticAnalyzer, language: str) -> dict[str, int]:
    cfg = results.get_cfg(language)
    references = results.results.get(language, {}).get("references", {})
    hierarchy = results.get_hierarchy(language)
    packages = results.get_package_dependencies(language)
    source_files = results.get_source_files(language)
    diagnostics = analyzer.collected_diagnostics.get(language, {})

    return {
        "references_count": len(references),
        "classes_count": len(hierarchy),
        "packages_count": len(packages),
        "call_graph_nodes": len(cfg.nodes),
        "call_graph_edges": len(cfg.edges),
        "source_files_count": len(source_files),
        "diagnostic_files_count": len(diagnostics),
        "diagnostics_count": sum(len(items) for items in diagnostics.values()),
    }


def _print_metrics(actual: dict[str, int], expected: dict[str, int]) -> None:
    print("\n" + "=" * 90)
    print("Python edge-cases integration metrics")
    print("=" * 90)
    print(f"{'Metric':<30} {'Expected':>12} {'Actual':>12} {'Status':>10}")
    print("-" * 90)
    for key in expected:
        status = "PASS" if actual[key] == expected[key] else "FAIL"
        print(f"{key:<30} {expected[key]:>12} {actual[key]:>12} {status:>10}")
    print("=" * 90)


@pytest.mark.integration
@pytest.mark.python_lang
def test_python_edge_cases_project_metrics_match_fixture(tmp_path):
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_metrics = expected["metrics"]

    scanner = ProjectScanner(PROJECT_PATH)
    discovered_languages = scanner.scan()
    discovered_language_keys = sorted({lang.lsp_server_key for lang in discovered_languages})
    assert (
        discovered_language_keys == expected["scanner_expected_languages"]
    ), f"Scanner language mismatch. Expected {expected['scanner_expected_languages']}, got {discovered_language_keys}"

    python_language = next(
        (lang.language for lang in discovered_languages if lang.lsp_server_key == "python"),
        None,
    )
    assert python_language is not None, "Python should be detected by ProjectScanner"

    analyzer = StaticAnalyzer(PROJECT_PATH)
    try:
        results = analyzer.analyze(cache_dir=tmp_path / "cache")
        actual_metrics = _extract_metrics(results, analyzer, python_language)
        _print_metrics(actual_metrics, expected_metrics)

        assert actual_metrics == expected_metrics

        references = results.results.get(python_language, {}).get("references", {})
        reference_keys = set(references.keys())
        for reference_name in expected.get("sample_references", []):
            assert reference_name in reference_keys, f"Expected reference '{reference_name}' not found"

        hierarchy_keys = set(results.get_hierarchy(python_language).keys())
        for class_name in expected.get("sample_classes", []):
            assert class_name in hierarchy_keys, f"Expected class '{class_name}' not found"

        edge_pairs = {(edge.get_source(), edge.get_destination()) for edge in results.get_cfg(python_language).edges}
        for source, destination in expected.get("required_edges", []):
            assert (source, destination) in edge_pairs, f"Expected edge '{source} -> {destination}' not found"
    finally:
        for client in analyzer.clients:
            try:
                client.close()
            except Exception:
                pass
