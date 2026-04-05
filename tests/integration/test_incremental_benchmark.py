"""Regression tests for incremental analysis using change scenarios.

These tests verify that the incremental analysis pipeline handles
different types of code changes correctly (escalation levels, additive
detection, cosmetic skip, etc.) and stays within timing bounds.

The target repo is auto-cloned at a pinned tag when it is not already
present in the sibling directory.  Set ``INCREMENTAL_BENCHMARK_REPO`` to
override the repo path.

Usage:
    uv run pytest tests/integration/test_incremental_benchmark.py -v -m incremental_benchmark
"""

import os
from pathlib import Path

import pytest

from tests.integration.incremental.repo_configs import REPO_CONFIGS, ensure_repo
from tests.integration.incremental.scenarios import SCENARIOS, ChangeScenario
from tests.integration.incremental.state_manager import StateManager
from tests.integration.incremental.metrics import run_incremental_with_metrics, IncrementalRunMetrics

# Max wall clock per scenario (generous bounds to avoid flakiness)
MAX_WALL_CLOCK: dict[str, float] = {
    "cosmetic_docstring": 30.0,
    "add_utility_function": 30.0,
    "modify_function_logic": 60.0,
    "add_parameter_cross_module": 60.0,
    "add_new_file": 60.0,
    "cross_component_change": 90.0,
    "delete_function": 60.0,
    "rename_across_files": 60.0,
}

_DEEPFACE_CONFIG = REPO_CONFIGS["deepface"]


def _resolve_repo_path() -> Path:
    """Return the deepface repo path, auto-cloning at the pinned tag if needed."""
    env_override = os.environ.get("INCREMENTAL_BENCHMARK_REPO")
    if env_override:
        return Path(env_override)
    return ensure_repo(_DEEPFACE_CONFIG)


def _repo_available() -> bool:
    try:
        repo = _resolve_repo_path()
    except Exception:
        return False
    return repo.exists() and (repo / ".git").exists() and (repo / ".codeboarding" / "checkpoints").exists()


skip_if_no_repo = pytest.mark.skipif(
    not _repo_available(),
    reason="Target repo not available or has no baseline checkpoint",
)


@pytest.fixture(scope="module")
def state_manager():
    """Module-scoped state manager (expensive baseline snapshot shared across tests)."""
    repo_dir = _resolve_repo_path()
    mgr = StateManager(repo_dir)
    mgr.snapshot_baseline()
    yield mgr
    mgr.cleanup()


@skip_if_no_repo
@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.incremental_benchmark
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_incremental_scenario(scenario: ChangeScenario, state_manager: StateManager) -> None:
    """Run a change scenario and verify escalation behavior + timing."""
    state_manager.apply_scenario(scenario)

    try:
        metrics = run_incremental_with_metrics(
            repo_dir=state_manager.repo_dir,
            output_dir=state_manager.output_dir,
        )
    finally:
        state_manager.restore_baseline()

    assert metrics.error is None, f"Incremental analysis failed: {metrics.error}"

    # Check expected escalation path
    if scenario.expected_additive:
        assert (
            metrics.purely_additive is True
        ), f"Expected purely additive but got escalation={metrics.escalation_level}"
        assert metrics.hops_used == 0

    if scenario.expected_escalation and scenario.expected_escalation not in ("additive_skip", "cosmetic_skip"):
        assert (
            metrics.escalation_level == scenario.expected_escalation
        ), f"Expected escalation={scenario.expected_escalation} but got {metrics.escalation_level}"

    # Timing bound
    max_time = MAX_WALL_CLOCK.get(scenario.name, 90.0)
    assert (
        metrics.wall_clock_seconds < max_time
    ), f"Scenario {scenario.name} took {metrics.wall_clock_seconds}s (max {max_time}s)"
