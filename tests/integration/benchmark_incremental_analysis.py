"""Benchmark script for incremental analysis performance.

Applies deterministic change scenarios to a target repo, runs incremental
analysis, and captures timing + pipeline metrics.

Usage:
    # Run all scenarios with 1 iteration
    uv run python tests/integration/benchmark_incremental_analysis.py --repo-path ../deepface

    # Run specific scenario with 3 iterations
    uv run python tests/integration/benchmark_incremental_analysis.py --repo-path ../deepface --scenario modify_function_logic --iterations 3

    # Save results to benchmark_results/
    uv run python tests/integration/benchmark_incremental_analysis.py --repo-path ../deepface --save-results
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

CODEBOARDING_ROOT = Path(__file__).parent.parent.parent.resolve()
BENCHMARK_RESULTS_DIR = CODEBOARDING_ROOT / "benchmark_results"

# Ensure CodeBoarding root is on sys.path for imports
if str(CODEBOARDING_ROOT) not in sys.path:
    sys.path.insert(0, str(CODEBOARDING_ROOT))

# Load .env for LLM API keys (mimics what main.py does)
from dotenv import load_dotenv

load_dotenv(CODEBOARDING_ROOT / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark incremental analysis scenarios")
    parser.add_argument(
        "--repo-path",
        type=Path,
        required=True,
        help="Path to the target repository (e.g., ../deepface)",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Run a specific scenario by name (default: all)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of iterations per scenario (default: 1)",
    )
    parser.add_argument(
        "--save-results",
        action="store_true",
        help="Save results to benchmark_results/ as JSON",
    )
    return parser.parse_args()


def run_scenario(state_manager, scenario, iteration: int) -> dict:
    """Apply scenario, run incremental analysis, capture metrics, restore."""
    from tests.integration.incremental.metrics import run_incremental_with_metrics

    state_manager.apply_scenario(scenario)

    metrics = run_incremental_with_metrics(
        repo_dir=state_manager.repo_dir,
        output_dir=state_manager.output_dir,
    )

    state_manager.restore_baseline()

    return metrics.to_dict()


def print_results(all_results: dict[str, list[dict]]) -> None:
    """Print a summary table of benchmark results."""
    header = f"  {'Scenario':<30} {'Time (s)':>10} {'Escalation':<18} {'Hops':>5} {'Components':>11} {'Deltas':>7}"
    print(f"\n{'=' * 95}")
    print("  Incremental Analysis Benchmark Results")
    print(f"{'=' * 95}\n")
    print(header)
    print(f"  {'-' * 91}")

    for scenario_name, runs in all_results.items():
        for i, r in enumerate(runs):
            suffix = f" [{i+1}]" if len(runs) > 1 else ""
            name = f"{scenario_name}{suffix}"
            error = r.get("error")
            if error:
                print(f"  {name:<30} {'ERROR':>10} {error[:40]}")
            else:
                print(
                    f"  {name:<30} {r['wall_clock_seconds']:>10.2f} "
                    f"{r['escalation_level']:<18} {r['hops_used']:>5} "
                    f"{r['components_affected']:>11} {r['file_deltas_count']:>7}"
                )

    # Print phase timing breakdown if available
    has_phases = any(r.get("phase_timings") for runs in all_results.values() for r in runs)
    if has_phases:
        print(f"\n{'=' * 110}")
        print("  Phase Timing Breakdown (seconds)")
        print(f"{'=' * 110}\n")
        phase_header = (
            f"  {'Scenario':<30} {'load_base':>10} {'compute_d':>10} "
            f"{'sem_trace':>10} {'escalation':>10} {'save_res':>10} {'other':>10}"
        )
        print(phase_header)
        print(f"  {'-' * 106}")

        for scenario_name, runs in all_results.items():
            for i, r in enumerate(runs):
                suffix = f" [{i+1}]" if len(runs) > 1 else ""
                name = f"{scenario_name}{suffix}"
                pt = r.get("phase_timings", {})
                if not pt:
                    continue
                total = r["wall_clock_seconds"]
                load_b = pt.get("load_baseline", 0)
                comp_d = pt.get("compute_delta", 0)
                sem_t = pt.get("semantic_trace", 0)
                esc = pt.get("determine_escalation", 0)
                save = pt.get("save_result", 0)
                other = round(total - load_b - comp_d - sem_t - esc - save, 3)
                print(
                    f"  {name:<30} {load_b:>10.3f} {comp_d:>10.3f} "
                    f"{sem_t:>10.3f} {esc:>10.3f} {save:>10.3f} {other:>10.3f}"
                )

    print()


def save_results(all_results: dict[str, list[dict]], repo_path: Path) -> Path:
    """Save benchmark results to JSON file."""
    BENCHMARK_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"incremental_{repo_path.name}_{timestamp}.json"
    filepath = BENCHMARK_RESULTS_DIR / filename

    data = {
        "repo": str(repo_path),
        "timestamp": timestamp,
        "scenarios": all_results,
    }
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    return filepath


def main() -> None:
    args = parse_args()
    repo_path = args.repo_path.resolve()

    if not (repo_path / ".git").exists():
        print(f"Error: {repo_path} is not a git repository", file=sys.stderr)
        sys.exit(1)

    from tests.integration.incremental.scenarios import SCENARIOS, SCENARIOS_BY_NAME
    from tests.integration.incremental.scenarios_langchain import LANGCHAIN_SCENARIOS, LANGCHAIN_SCENARIOS_BY_NAME
    from tests.integration.incremental.scenarios_markitdown import MARKITDOWN_SCENARIOS, MARKITDOWN_SCENARIOS_BY_NAME
    from tests.integration.incremental.state_manager import StateManager

    # Detect repo type from directory name to pick the right scenario set
    repo_name = repo_path.name.lower()
    if "markitdown" in repo_name:
        all_scenarios = MARKITDOWN_SCENARIOS
        all_by_name = MARKITDOWN_SCENARIOS_BY_NAME
    elif "langchain" in repo_name:
        all_scenarios = LANGCHAIN_SCENARIOS
        all_by_name = LANGCHAIN_SCENARIOS_BY_NAME
    else:
        all_scenarios = SCENARIOS
        all_by_name = SCENARIOS_BY_NAME

    # Select scenarios
    if args.scenario:
        if args.scenario not in all_by_name:
            print(
                f"Unknown scenario: {args.scenario}. Available: {', '.join(all_by_name.keys())}",
                file=sys.stderr,
            )
            sys.exit(1)
        selected = [all_by_name[args.scenario]]
    else:
        selected = all_scenarios

    print(f"Repo: {repo_path}")
    print(f"Scenarios: {', '.join(s.name for s in selected)}")
    print(f"Iterations: {args.iterations}")

    state = StateManager(repo_path)
    state.snapshot_baseline()

    all_results: dict[str, list[dict]] = {}

    try:
        for scenario in selected:
            print(f"\n--- {scenario.name}: {scenario.description} ---")
            runs = []
            for i in range(args.iterations):
                label = f"  Iteration {i+1}/{args.iterations}" if args.iterations > 1 else "  Running"
                print(f"{label}...", end=" ", flush=True)
                try:
                    result = run_scenario(state, scenario, i)
                    if result.get("error"):
                        print(f"ERROR: {result['error'][:60]}")
                    else:
                        print(
                            f"time={result['wall_clock_seconds']}s "
                            f"escalation={result['escalation_level']} "
                            f"hops={result['hops_used']}"
                        )
                    runs.append(result)
                except Exception as e:
                    print(f"FAILED: {e}")
                    runs.append({"error": str(e), "wall_clock_seconds": 0})
                    state.restore_baseline()

            all_results[scenario.name] = runs

        print_results(all_results)

        if args.save_results:
            filepath = save_results(all_results, repo_path)
            print(f"Results saved to: {filepath}")

    finally:
        state.cleanup()


if __name__ == "__main__":
    main()
