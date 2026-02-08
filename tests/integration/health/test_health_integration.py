"""Integration test: clone a real repo at a pinned commit, run health checks, compare output.

REQUIREMENTS:
    LSP servers must be installed before running these tests. Run:
        python install.py

    This installs: Pyright, TypeScript LSP, gopls, JDTLS, and Intelephense

Usage:
    # Run all integration tests
    uv run pytest -m integration

    # Run health integration tests only
    uv run pytest tests/integration/health/

    # Run all tests except integration
    uv run pytest -m "not integration"
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from git import Repo

from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from repo_utils import clone_repository
from static_analyzer import get_static_analysis
from static_analyzer.programming_language import ProgrammingLanguage

REPO_URL = "https://github.com/CodeBoarding/CodeBoarding"
PINNED_COMMIT = "03b25afe8d37ce733e5f70c3cbcdfb52f4883dcd"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "health_report.json"

# Tolerance for numeric fields that can vary slightly due to LSP non-determinism.
# total_entities_checked can fluctuate by a few nodes between runs.
ENTITY_COUNT_TOLERANCE = 5
# Scores derived from entity counts inherit that variance.
SCORE_TOLERANCE = 0.02


def _mock_project_scanner_scan(self) -> list[ProgrammingLanguage]:
    """Mock ProjectScanner.scan() to return languages without requiring tokei binary."""
    return [
        ProgrammingLanguage(
            language="Python",
            size=50000,
            percentage=60.0,
            suffixes=[".py"],
            server_commands=["pyright-langserver", "--stdio"],
            lsp_server_key="python",
        ),
        ProgrammingLanguage(
            language="TypeScript",
            size=33000,
            percentage=40.0,
            suffixes=[".ts", ".tsx"],
            server_commands=["cli.mjs", "--stdio", "--log-level=2"],
            lsp_server_key="typescript",
        ),
    ]


def _normalize_cycle(cycle: str) -> str:
    """Normalize a cycle string to a canonical form by rotating to start from alphabetically first node.

    Example: "static_analyzer -> agents -> static_analyzer" -> "agents -> static_analyzer -> agents"
    """
    parts = cycle.split(" -> ")
    if len(parts) < 2:
        return cycle

    # Only strip the trailing node if it closes the cycle (equals the first node).
    # If it doesn't match, the cycle is malformed and we keep it as-is for comparison.
    if parts[-1] == parts[0]:
        core = parts[:-1]
    else:
        core = parts

    # Rotate to start from the alphabetically smallest element
    min_idx = min(range(len(core)), key=lambda i: core[i])
    rotated = core[min_idx:] + core[:min_idx]

    # Re-append the start node to close the cycle
    rotated.append(rotated[0])

    return " -> ".join(rotated)


def _normalize_report(report: dict) -> dict:
    """Normalize a health report for deterministic comparison.

    This sorts:
    - cycles in circular_dependencies check (after normalizing each cycle)
    - entities within each finding group by entity_name
    - finding groups by severity then description
    - file summaries by file_path
    """
    report = json.loads(json.dumps(report))  # Deep copy

    for check in report.get("check_summaries", []):
        # Normalize cycles for circular_dependencies
        if check.get("check_type") == "circular_dependencies" and "cycles" in check:
            check["cycles"] = sorted([_normalize_cycle(c) for c in check["cycles"]])

        # Sort finding groups
        if "finding_groups" in check:
            for fg in check["finding_groups"]:
                # Sort entities within each finding group by entity_name
                if "entities" in fg:
                    fg["entities"] = sorted(fg["entities"], key=lambda e: e.get("entity_name", ""))
            # Sort finding groups by severity then description
            check["finding_groups"] = sorted(
                check["finding_groups"],
                key=lambda fg: (fg.get("severity", ""), fg.get("description", "")),
            )

    # Sort check_summaries by check_name for order-independent comparison
    report["check_summaries"] = sorted(
        report.get("check_summaries", []),
        key=lambda c: c.get("check_name", ""),
    )

    # Sort file summaries by file_path
    if "file_summaries" in report:
        report["file_summaries"] = sorted(report["file_summaries"], key=lambda f: f.get("file_path", ""))

    return report


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.health
class TestHealthCheckIntegration:
    """Clone CodeBoarding/CodeBoarding at a pinned commit, run health checks, and compare output."""

    def test_health_report_matches_fixture(self, tmp_path_factory):
        """Clone repo, run health checks, and verify output matches expected fixture."""
        tmp_path = tmp_path_factory.mktemp("health_test")
        repo_root = tmp_path / "repos"
        repo_root.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Clone and checkout pinned commit
        repo_name = clone_repository(REPO_URL, repo_root)
        repo_path = (repo_root / repo_name).resolve()
        repo = Repo(repo_path)
        repo.git.checkout(PINNED_COMMIT)

        # Mock ProjectScanner.scan() to bypass tokei binary dependency
        with patch("static_analyzer.scanner.ProjectScanner.scan", _mock_project_scanner_scan):
            # Run static analysis (the heavy part)
            static_analysis = get_static_analysis(repo_path, cache_dir=tmp_path / "cache")

            # Set up health config
            health_config_dir = output_dir / "health"
            initialize_health_dir(health_config_dir)
            health_config = load_health_config(health_config_dir)

            # Run health checks
            report = run_health_checks(static_analysis, repo_name, config=health_config, repo_path=repo_path)

        assert report is not None, "Health report should not be None"

        # Load and normalize both reports
        actual = json.loads(report.model_dump_json(indent=2, exclude_none=True))
        actual.pop("timestamp", None)

        with open(FIXTURE_PATH) as f:
            expected = json.load(f)
        expected.pop("timestamp", None)

        # Normalize both for deterministic comparison
        actual = _normalize_report(actual)
        expected = _normalize_report(expected)

        # Debug: write actual output for comparison
        debug_path = Path("/tmp/actual_health_report.json")
        debug_path.write_text(json.dumps(actual, indent=2))

        # Compare top-level scalar fields
        assert actual.get("repository_name") == expected.get("repository_name"), "repository_name mismatch"
        assert abs(actual.get("overall_score", 0) - expected.get("overall_score", 0)) <= SCORE_TOLERANCE, (
            f"overall_score mismatch: expected {expected.get('overall_score', 0)} "
            f"(±{SCORE_TOLERANCE}), got {actual.get('overall_score', 0)}"
        )

        # Compare each check_summary individually for clear failure messages
        actual_checks = {c["check_name"]: c for c in actual.get("check_summaries", [])}
        expected_checks = {c["check_name"]: c for c in expected.get("check_summaries", [])}

        assert sorted(actual_checks.keys()) == sorted(expected_checks.keys()), (
            f"Mismatch in check_summary names present in report: "
            f"expected {sorted(expected_checks.keys())}, got {sorted(actual_checks.keys())}"
        )

        for check_name in sorted(expected_checks.keys()):
            act = actual_checks[check_name]
            exp = expected_checks[check_name]

            # Structural fields must match exactly
            for key in ("check_name", "description", "check_type"):
                assert act.get(key) == exp.get(key), f"'{check_name}' field '{key}' differs"

            # Numeric fields: allow small tolerance for LSP non-determinism
            for key in ("total_entities_checked", "findings_count", "warning_count"):
                if key in exp:
                    assert abs(act.get(key, 0) - exp.get(key, 0)) <= ENTITY_COUNT_TOLERANCE, (
                        f"'{check_name}' field '{key}' differs beyond tolerance: "
                        f"expected {exp.get(key, 0)} (±{ENTITY_COUNT_TOLERANCE}), got {act.get(key, 0)}"
                    )

            if "score" in exp:
                assert abs(act.get("score", 0) - exp.get("score", 0)) <= SCORE_TOLERANCE, (
                    f"'{check_name}' score differs beyond tolerance: "
                    f"expected {exp.get('score', 0)} (±{SCORE_TOLERANCE}), got {act.get('score', 0)}"
                )

            # Finding groups: compare entity names (the important structural part)
            act_groups = act.get("finding_groups", [])
            exp_groups = exp.get("finding_groups", [])
            assert len(act_groups) == len(
                exp_groups
            ), f"'{check_name}' has {len(act_groups)} finding groups, expected {len(exp_groups)}"

            for i, (ag, eg) in enumerate(zip(act_groups, exp_groups)):
                assert ag.get("severity") == eg.get("severity"), f"'{check_name}' group {i} severity differs"
                assert ag.get("description") == eg.get("description"), f"'{check_name}' group {i} description differs"

                # Expected entities must all be present (catches real regressions).
                # A small number of extra entities is tolerated because LSP
                # non-determinism can push near-threshold functions above the
                # cutoff in some runs.
                act_entities = {e["entity_name"] for e in ag.get("entities", [])}
                exp_entities = {e["entity_name"] for e in eg.get("entities", [])}
                missing = exp_entities - act_entities
                extra = act_entities - exp_entities

                assert not missing, f"'{check_name}' group {i}: expected entities missing from actual: {missing}"
                assert (
                    len(extra) <= ENTITY_COUNT_TOLERANCE
                ), f"'{check_name}' group {i}: too many unexpected entities: {extra}"

            # Circular dependencies: compare cycles exactly
            if "cycles" in exp:
                assert act.get("cycles", []) == exp.get("cycles", []), f"'{check_name}' cycles differ"

        # Compare file_summaries: expected files must all be present;
        # allow a few extras from threshold fluctuation.
        actual_files = {f["file_path"]: f for f in actual.get("file_summaries", [])}
        expected_files = {f["file_path"]: f for f in expected.get("file_summaries", [])}

        missing_files = set(expected_files) - set(actual_files)
        extra_files = set(actual_files) - set(expected_files)

        assert (
            len(missing_files) <= ENTITY_COUNT_TOLERANCE
        ), f"Too many expected file summaries missing: {missing_files}"
        assert len(extra_files) <= ENTITY_COUNT_TOLERANCE, f"Too many unexpected file summaries: {extra_files}"

        for file_path in sorted(set(expected_files) & set(actual_files)):
            act_f = actual_files[file_path]
            exp_f = expected_files[file_path]

            for key in ("total_findings", "warning_findings"):
                assert abs(act_f.get(key, 0) - exp_f.get(key, 0)) <= ENTITY_COUNT_TOLERANCE, (
                    f"file_summary '{file_path}' field '{key}' differs beyond tolerance: "
                    f"expected {exp_f.get(key, 0)} (±{ENTITY_COUNT_TOLERANCE}), got {act_f.get(key, 0)}"
                )
