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
"""

import time

import pytest
from git import Repo
from unittest.mock import patch

from static_analyzer import get_static_analysis
from repo_utils import clone_repository

from .conftest import (
    RepositoryTestConfig,
    REPOSITORY_CONFIGS,
    create_mock_scanner,
    load_fixture,
    extract_metrics,
)

# Tolerance percentage for metric comparisons (2% = 0.02)
METRIC_TOLERANCE = 0.02

# Minimum absolute tolerance for small numbers (e.g., 20 vs 19 is 5% diff, but only 1 unit)
MIN_ABSOLUTE_TOLERANCE = 2

# Tolerance percentage for execution time comparisons (10% = 0.10)
EXECUTION_TIME_TOLERANCE = 0.15


def get_language_marker(language: str):
    """Get the pytest marker for a given language."""
    marker_map = {
        "Python": pytest.mark.python_lang,
        "Java": pytest.mark.java_lang,
        "Go": pytest.mark.go_lang,
        "TypeScript": pytest.mark.typescript_lang,
        "PHP": pytest.mark.php_lang,
        "JavaScript": pytest.mark.javascript_lang,
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
    ):
        """Verify that static analysis produces expected results.

        This test:
        1. Clones the repository at the pinned commit
        2. Clears cache by using a fresh temp directory
        3. Runs static analysis with mocked language detection
        4. Verifies the expected language is present in results
        5. Compares metrics against expected fixture with 1% tolerance
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

        # Verify the expected language is present in results
        actual_languages = static_analysis.get_languages()
        assert (
            config.language in actual_languages
        ), f"Expected language '{config.language}' not in results. Found: {actual_languages}"

        # Extract actual metrics
        actual_metrics = extract_metrics(static_analysis, config.language)
        actual_metrics["execution_time_seconds"] = actual_execution_time

        # Compare all metrics and collect results
        metric_names = [
            "references_count",
            "classes_count",
            "packages_count",
            "call_graph_nodes",
            "call_graph_edges",
            "source_files_count",
            "execution_time_seconds",
        ]

        results = []
        for metric_name in metric_names:
            actual = actual_metrics[metric_name]
            expected_val = expected_metrics[metric_name]
            tolerance = EXECUTION_TIME_TOLERANCE if metric_name == "execution_time_seconds" else METRIC_TOLERANCE
            is_pass, diff_info = self._check_metric_within_tolerance(actual, expected_val, tolerance)
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
        if "sample_classes" in expected:
            self._verify_sample_classes_present(static_analysis, config.language, expected["sample_classes"])

    def _check_metric_within_tolerance(
        self,
        actual: int | float,
        expected: int | float,
        tolerance: float,
    ) -> tuple[bool, str]:
        """Check if actual value is within tolerance of expected.

        Returns:
            Tuple of (is_pass, diff_info_string)
        """
        if expected == 0:
            if actual == 0:
                return True, "match"
            return False, f"expected 0, got {actual}"

        relative_diff = abs(actual - expected) / expected
        absolute_diff = abs(actual - expected)

        # For small numbers, use absolute tolerance; for large numbers, use percentage
        # Whichever is more generous
        if absolute_diff <= MIN_ABSOLUTE_TOLERANCE:
            return True, f"±{absolute_diff} (within ±{MIN_ABSOLUTE_TOLERANCE})"

        if relative_diff <= tolerance:
            return True, f"±{relative_diff * 100:.1f}%"

        diff = actual - expected
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

        reference_keys = set(references.keys())

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
