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
    def test_metrics_match_fixture(
        self,
        config: RepositoryTestConfig,
        temp_workspace,
    ):
        """Verify that static analysis metrics match expected fixture values.

        This test:
        1. Clones the repository at the pinned commit
        2. Clears cache by using a fresh temp directory
        3. Runs static analysis with mocked language detection
        4. Compares metrics against expected fixture with exact matching
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

        # Run static analysis with mocked scanner
        mock_scan = create_mock_scanner(config.mock_language)
        with patch("static_analyzer.scanner.ProjectScanner.scan", mock_scan):
            static_analysis = get_static_analysis(repo_path, cache_dir=cache_dir)

        # Extract actual metrics
        actual_metrics = extract_metrics(static_analysis, config.language)

        # Compare with exact matching (0 tolerance)
        self._assert_metric_exact_match(
            actual_metrics["references_count"],
            expected_metrics["references_count"],
            "references_count",
        )
        self._assert_metric_exact_match(
            actual_metrics["classes_count"],
            expected_metrics["classes_count"],
            "classes_count",
        )
        self._assert_metric_exact_match(
            actual_metrics["packages_count"],
            expected_metrics["packages_count"],
            "packages_count",
        )
        self._assert_metric_exact_match(
            actual_metrics["call_graph_nodes"],
            expected_metrics["call_graph_nodes"],
            "call_graph_nodes",
        )
        self._assert_metric_exact_match(
            actual_metrics["call_graph_edges"],
            expected_metrics["call_graph_edges"],
            "call_graph_edges",
        )
        self._assert_metric_exact_match(
            actual_metrics["source_files_count"],
            expected_metrics["source_files_count"],
            "source_files_count",
        )

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

    def _assert_metric_exact_match(
        self,
        actual: int,
        expected: int,
        metric_name: str,
    ):
        """Assert that actual value exactly matches expected."""
        assert actual == expected, f"{metric_name}: expected {expected}, got {actual} (diff: {actual - expected})"

    def _verify_sample_entities_present(
        self,
        static_analysis,
        language: str,
        sample_entities: list[str],
        entity_type: str,
    ):
        """Verify that sample entities are present in the analysis results."""
        references = static_analysis.results.get(language, {}).get("references", {})
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


@pytest.mark.integration
class TestStaticAnalysisResultStructure:
    """Test that static analysis results have expected structure."""

    @pytest.mark.parametrize("config", REPOSITORY_CONFIGS, ids=lambda c: c.name)
    def test_language_present_in_results(self, config: RepositoryTestConfig, temp_workspace):
        """Verify the expected language is present in results."""
        repo_root = temp_workspace / "repos"
        repo_root.mkdir()
        cache_dir = temp_workspace / "cache"
        cache_dir.mkdir()

        repo_name = clone_repository(config.repo_url, repo_root)
        repo_path = (repo_root / repo_name).resolve()
        repo = Repo(repo_path)
        repo.git.checkout(config.pinned_commit)

        mock_scan = create_mock_scanner(config.mock_language)
        with patch("static_analyzer.scanner.ProjectScanner.scan", mock_scan):
            static_analysis = get_static_analysis(repo_path, cache_dir=cache_dir)

        actual_languages = static_analysis.get_languages()
        assert (
            config.language in actual_languages
        ), f"Expected language '{config.language}' not in results. Found: {actual_languages}"
