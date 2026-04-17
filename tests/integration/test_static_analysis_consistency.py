"""Integration tests verifying static analysis consistency across multiple languages.

These tests clone real repositories at pinned commits and verify that static analysis
produces consistent metrics. They are designed to:
- NOT run on every commit (use -m "not integration" to skip)
- Run manually OR upon merge to main
- Be executable per-language or all together

REQUIREMENTS:
    LSP servers must be installed before running these tests. Run:
        python install.py

    This installs: Pyright, TypeScript LSP, gopls, JDTLS, and Intelephense

Usage:
    # Run all integration tests
    uv run pytest -m integration

    # Run Python language tests only
    uv run pytest -m "integration and python_lang"

    # Run all tests except integration
    uv run pytest -m "not integration"

    # Write snapshots for manual validation (writes to tests/integration/snapshots/real_projects/)
    uv run pytest tests/integration/test_static_analysis_consistency.py -m integration --write-snapshots
"""

import json
import platform
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from git import Repo

from repo_utils import clone_repository
from repo_utils.ignore import initialize_codeboardingignore
from static_analyzer import get_static_analysis
from static_analyzer.analysis_result import StaticAnalysisResults

from .conftest import (
    REPOSITORY_CONFIGS,
    RepositoryTestConfig,
    create_mock_scanner,
    extract_metrics,
    load_fixture,
)

SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "real_projects"


def _relative_path(file_path: str, repo_path: Path) -> str:
    """Return a repo-relative path string, falling back to the original if it's not under repo_path.

    Uses as_posix() so snapshots written on Windows are byte-identical to
    those written on macOS / Linux — otherwise a Windows-authored snapshot
    would diff against the repo version on every subsequent CI run.
    """
    if not file_path:
        return ""
    try:
        return Path(file_path).relative_to(repo_path).as_posix()
    except ValueError:
        return file_path


def _write_snapshot(static_analysis: StaticAnalysisResults, language: str, config_name: str, repo_path: Path) -> Path:
    """Write a detailed snapshot of the analysis results to a JSON file for manual validation.

    The snapshot includes all references, hierarchy, call graph edges, package dependencies,
    and source files — everything needed to verify correctness by inspection.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = repo_path.resolve()

    # References: sorted list of fully qualified names with type and location
    refs = static_analysis.results.get(language, {}).get("references", {})
    references_snapshot = []
    for fqn, node in sorted(refs.items()):
        references_snapshot.append(
            {
                "name": fqn,
                "type": node.entity_label(),
                "file": _relative_path(node.file_path, repo_path),
                "lines": f"{node.line_start}-{node.line_end}",
            }
        )

    # Call graph edges
    try:
        cfg = static_analysis.get_cfg(language)
        edges_snapshot = sorted([e.get_source(), e.get_destination()] for e in cfg.edges)
        nodes_snapshot = sorted(cfg.nodes.keys())
    except ValueError:
        edges_snapshot = []
        nodes_snapshot = []

    # Package dependencies
    try:
        deps = static_analysis.get_package_dependencies(language)
    except ValueError:
        deps = {}
    packages_snapshot = {}
    for pkg_name, pkg_info in sorted(deps.items()):
        packages_snapshot[pkg_name] = {
            "imports": sorted(pkg_info.get("imports", [])),
            "imported_by": sorted(pkg_info.get("imported_by", [])),
        }

    # Source files
    source_files = static_analysis.get_source_files(language)
    source_files_rel = sorted(_relative_path(f, repo_path) for f in source_files)

    snapshot = {
        "config_name": config_name,
        "language": language,
        "metrics": {
            "references_count": len(refs),
            "packages_count": len(deps),
            "call_graph_nodes": len(nodes_snapshot),
            "call_graph_edges": len(edges_snapshot),
            "source_files_count": len(source_files_rel),
        },
        "references": references_snapshot,
        "call_graph_nodes": nodes_snapshot,
        "call_graph_edges": edges_snapshot,
        "package_dependencies": packages_snapshot,
        "source_files": source_files_rel,
    }

    snapshot_path = SNAPSHOT_DIR / f"{config_name}_snapshot.json"
    with open(snapshot_path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)

    return snapshot_path


# Tolerance for metric vs fixture (relative diff) to account for LSP variance on Windows.
METRIC_TOLERANCE = 0.026

# Minimum absolute tolerance for small numbers (e.g., 20 vs 19 is 5% diff, but only 1 unit)
MIN_ABSOLUTE_TOLERANCE = 2

# Upper-bound tolerance for execution time (15% slower than baseline is still a pass).
# Faster runs never fail; hardware gets quicker, so we only gate on slowdowns.
EXECUTION_TIME_TOLERANCE = 0.15

# Minimum absolute tolerance for execution-time comparisons; the larger
# of this and EXECUTION_TIME_TOLERANCE applies. Set to 150s to absorb
# JDTLS warm-up variance on shared macOS runners (observed 177s-295s
# range for the same mockito_java test across consecutive runs).
MIN_EXECUTION_TIME_TOLERANCE = 150


def get_language_marker(language: str):
    """Get the pytest marker for a given language."""
    marker_map = {
        "Python": pytest.mark.python_lang,
        "Java": pytest.mark.java_lang,
        "Go": pytest.mark.go_lang,
        "TypeScript": pytest.mark.typescript_lang,
        "PHP": pytest.mark.php_lang,
        "JavaScript": pytest.mark.javascript_lang,
        "Rust": pytest.mark.rust_lang,
        "CSharp": pytest.mark.csharp_lang,
    }
    return marker_map.get(language)


def generate_test_params():
    """Generate pytest.param entries with markers for each config."""
    params = []
    for config in REPOSITORY_CONFIGS:
        markers = [
            pytest.mark.integration,
            pytest.mark.slow,
        ]
        lang_marker = get_language_marker(config.language)
        if lang_marker:
            markers.append(lang_marker)

        params.append(pytest.param(config, marks=markers, id=config.name))
    return params


@pytest.mark.integration
@pytest.mark.slow
class TestStaticAnalysisConsistency:
    """Test class for static analysis consistency verification."""

    @pytest.mark.parametrize("config", generate_test_params())
    def test_static_analysis_matches_fixture(
        self,
        config: RepositoryTestConfig,
        temp_workspace,
        request,
    ):
        """Verify that static analysis produces expected results.

        This test:
        1. Clones the repository at the pinned commit
        2. Clears cache by using a fresh temp directory
        3. Runs static analysis with mocked language detection
        4. Verifies the expected language is present in results
        5. Compares metrics against expected fixture within METRIC_TOLERANCE
        6. Optionally writes a detailed snapshot (--write-snapshots)
        """
        # Setup directories
        repo_root = temp_workspace / "repos"
        repo_root.mkdir()
        cache_dir = temp_workspace / "cache"
        cache_dir.mkdir()

        # Clone and checkout pinned commit
        repo_name = clone_repository(config.repo_url, repo_root)
        repo_path = (repo_root / repo_name).resolve()
        repo = Repo(repo_path)
        repo.git.checkout(config.pinned_commit)

        # Ensure the current .codeboardingignore template is used, not whatever
        # the cloned repo might have from an older version.
        codeboarding_dir = repo_path / ".codeboarding"
        codeboarding_dir.mkdir(parents=True, exist_ok=True)
        ignore_file = codeboarding_dir / ".codeboardingignore"
        ignore_file.unlink(missing_ok=True)
        initialize_codeboardingignore(codeboarding_dir)

        # Load expected fixture
        expected = load_fixture(config.fixture_file)
        expected_metrics = expected["metrics"]

        # Run static analysis with mocked scanner and measure execution time
        mock_scan = create_mock_scanner(config.mock_language)
        start_time = time.perf_counter()
        with patch("static_analyzer.scanner.ProjectScanner.scan", mock_scan):
            static_analysis = get_static_analysis(repo_path, cache_dir=cache_dir)
        end_time = time.perf_counter()
        actual_execution_time = end_time - start_time

        # Write snapshot if requested
        if request.config.getoption("--write-snapshots"):
            snapshot_path = _write_snapshot(static_analysis, config.language, config.name, repo_path)
            print(f"\nSnapshot written to: {snapshot_path}")

        # Extract actual metrics
        actual_metrics = extract_metrics(static_analysis, config.language)
        actual_metrics["execution_time_seconds"] = actual_execution_time

        # Compare all metrics and collect results
        metric_names = [
            "references_count",
            "packages_count",
            "call_graph_nodes",
            "call_graph_edges",
            "source_files_count",
            "execution_time_seconds",
        ]

        current_os = platform.system()
        # Metrics that vary across OSes (LSP servers report slightly
        # different reference counts on Windows; execution time tracks
        # runner hardware) are stored as ``<name>_by_os`` dicts keyed by
        # ``platform.system()``. All other metrics are flat scalars.
        per_os_metrics = {"references_count", "execution_time_seconds"}
        results = []
        for metric_name in metric_names:
            actual = actual_metrics[metric_name]
            if metric_name in per_os_metrics:
                by_os_key = f"{metric_name}_by_os"
                try:
                    expected_val = expected_metrics[by_os_key][current_os]
                except KeyError as e:
                    raise AssertionError(
                        f"Fixture {config.fixture_file} is missing " f"{by_os_key}[{current_os!r}] (got {e})"
                    ) from None
            else:
                expected_val = expected_metrics[metric_name]
            if metric_name == "execution_time_seconds":
                tolerance = EXECUTION_TIME_TOLERANCE
                min_absolute = MIN_EXECUTION_TIME_TOLERANCE
                # Faster-than-baseline runs are a win, not a regression — only
                # flag when ``actual`` exceeds the upper tolerance bound.
                upper_only = True
            else:
                tolerance = METRIC_TOLERANCE
                min_absolute = MIN_ABSOLUTE_TOLERANCE
                upper_only = False
            is_pass, diff_info = self._check_metric_within_tolerance(
                actual, expected_val, tolerance, min_absolute, upper_only=upper_only
            )
            results.append(
                {
                    "metric": metric_name,
                    "actual": actual,
                    "expected": expected_val,
                    "is_pass": is_pass,
                    "diff_info": diff_info,
                }
            )

        # Display all metrics with status
        self._display_metric_comparison(results, config.name)

        # Assert all metrics pass
        failed_metrics = [r for r in results if not r["is_pass"]]
        if failed_metrics:
            failure_msgs = [
                f"  - {r['metric']}: expected {r['expected']}, got {r['actual']} ({r['diff_info']})"
                for r in failed_metrics
            ]
            pytest.fail(f"Metric comparison failed for {config.name}:\n" + "\n".join(failure_msgs))

        # Verify sample entities are present (if defined in fixture)
        if "sample_references" in expected:
            self._verify_sample_entities_present(
                static_analysis,
                config.language,
                expected["sample_references"],
                "references",
            )

    def _check_metric_within_tolerance(
        self,
        actual: int | float,
        expected: int | float,
        tolerance: float,
        min_absolute: int | float = MIN_ABSOLUTE_TOLERANCE,
        upper_only: bool = False,
    ) -> tuple[bool, str]:
        """Check if actual value is within tolerance of expected.

        When ``upper_only`` is True, ``actual < expected`` is always a pass —
        used for metrics (e.g. execution time) where beating the baseline
        is a win rather than a regression.

        Returns:
            Tuple of (is_pass, diff_info_string)
        """
        if expected == 0:
            if actual == 0:
                return True, "match"
            if upper_only and actual < 0:
                return True, f"faster than baseline ({actual})"
            return False, f"expected 0, got {actual}"

        diff = actual - expected
        absolute_diff = abs(diff)
        relative_diff = absolute_diff / expected

        if upper_only and actual < expected:
            return True, f"faster than baseline (-{relative_diff * 100:.1f}%)"

        # For small numbers, use absolute tolerance; for large numbers, use percentage
        # Whichever is more generous
        if absolute_diff <= min_absolute:
            return True, f"±{absolute_diff} (within ±{min_absolute})"

        if relative_diff <= tolerance:
            return True, f"±{relative_diff * 100:.1f}%"

        diff_str = f"{diff:+.0f}" if isinstance(diff, int) or diff == int(diff) else f"{diff:+.2f}"
        return (
            False,
            f"diff: {diff_str}, {relative_diff * 100:.1f}% (>{tolerance * 100:.0f}%)",
        )

    def _display_metric_comparison(self, results: list[dict], repo_name: str):
        """Display all metric comparisons in a formatted table."""
        print(f"\n{'=' * 80}")
        print(f"Metric Comparison for {repo_name}")
        print(f"{'=' * 80}")
        print(f"{'Metric':<25} {'Expected':>12} {'Actual':>12} {'Status':>10} {'Details':>18}")
        print(f"{'-' * 80}")

        for r in results:
            status = "PASS" if r["is_pass"] else "FAIL"
            print(f"{r['metric']:<25} {r['expected']:>12} {r['actual']:>12} {status:>10} {r['diff_info']:>18}")

        print(f"{'=' * 80}")

    def _verify_sample_entities_present(
        self,
        static_analysis,
        language: str,
        sample_entities: list[str],
        entity_type: str,
    ):
        """Verify that sample entities are present in the analysis results."""
        lang_results = static_analysis.results.get(language, {})
        if not isinstance(lang_results, dict):
            pytest.fail(f"Expected dict for {language} results, got {type(lang_results).__name__}")

        references = lang_results.get("references", {})
        if not isinstance(references, dict):
            pytest.fail(f"Expected dict for references, got {type(references).__name__}")

        reference_keys = {k.lower() for k in references.keys()}

        for entity in sample_entities:
            entity_lower = entity.lower()
            assert entity_lower in reference_keys, f"Expected {entity_type} '{entity}' not found in {language} analysis"

    def _verify_sample_classes_present(
        self,
        static_analysis,
        language: str,
        sample_classes: list[str],
    ):
        """Verify that sample classes are present in the hierarchy."""
        try:
            hierarchy = static_analysis.get_hierarchy(language)
            hierarchy_keys = set(hierarchy.keys())
        except ValueError:
            hierarchy_keys = set()

        for cls in sample_classes:
            assert cls in hierarchy_keys, f"Expected class '{cls}' not found in {language} hierarchy"
