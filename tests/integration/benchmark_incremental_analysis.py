"""Benchmark script for incremental analysis performance.

Applies deterministic change scenarios to a target repo, runs incremental
analysis, and captures timing + pipeline metrics.

The target repo is auto-cloned at a pinned tag if it doesn't already exist
in the sibling directory.  Pass ``--repo-path`` to override with a custom
location.

Usage:
    # Run all deepface scenarios (auto-clones if needed)
    uv run python tests/integration/benchmark_incremental_analysis.py

    # Run markitdown scenarios
    uv run python tests/integration/benchmark_incremental_analysis.py --repo-name markitdown

    # Run specific scenario with 3 iterations
    uv run python tests/integration/benchmark_incremental_analysis.py --scenario modify_function_logic --iterations 3

    # Use a custom clone location
    uv run python tests/integration/benchmark_incremental_analysis.py --repo-path /tmp/deepface

    # Save results to benchmark_results/
    uv run python tests/integration/benchmark_incremental_analysis.py --save-results
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
        "--repo-name",
        type=str,
        default="deepface",
        choices=["deepface", "langchain", "markitdown", "jsoup", "zustand"],
        help="Target repo to benchmark (default: deepface). Auto-clones at pinned tag if needed.",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=None,
        help="Override: path to an already-cloned target repository",
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
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write results JSON to this exact path (implies --save-results)",
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

    result = metrics.to_dict()
    result["expected"] = scenario.expected_outcome or ""
    return result


def print_results(all_results: dict[str, list[dict]]) -> None:
    """Print a summary table of benchmark results."""
    header = f"  {'Scenario':<30} {'Time (s)':>10} {'Outcome':<10} {'Expected':<10} {'Hops':>5} {'Components':>11} {'Deltas':>7}"
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
                expected = r.get("expected", "")
                match = "" if not expected else (" ✓" if r["outcome"] == expected else " ✗")
                print(
                    f"  {name:<30} {r['wall_clock_seconds']:>10.2f} "
                    f"{r['outcome']:<10} {expected:<10}{match}"
                    f" {r['hops_used']:>5} "
                    f"{r['components_affected']:>11} {r['file_deltas_count']:>7}"
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

    from tests.integration.incremental.repo_configs import REPO_CONFIGS, ensure_repo, get_scenario_module
    from tests.integration.incremental.state_manager import StateManager

    config = REPO_CONFIGS[args.repo_name]

    if args.repo_path is not None:
        repo_path = args.repo_path.resolve()
        if not (repo_path / ".git").exists():
            print(f"Error: {repo_path} is not a git repository", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Ensuring {config.name} is cloned at {config.pinned_tag} ...")
        repo_path = ensure_repo(config)

    all_scenarios, all_by_name = get_scenario_module(config)

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

    print(f"Repo: {repo_path} (pinned: {config.pinned_tag})")
    print(f"Scenarios: {', '.join(s.name for s in selected)}")
    print(f"Iterations: {args.iterations}")

    state = StateManager(repo_path)
    state.verify_pinned_commit(config.pinned_tag)
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
                            f"outcome={result['outcome']} "
                            f"hops={result['hops_used']}"
                        )
                    runs.append(result)
                except Exception as e:
                    print(f"FAILED: {e}")
                    runs.append({"error": str(e), "wall_clock_seconds": 0})
                    state.restore_baseline()

            all_results[scenario.name] = runs

        print_results(all_results)

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "repo": str(repo_path),
                "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                "scenarios": all_results,
            }
            with open(args.output, "w") as f:
                json.dump(data, f, indent=2)
            print(f"Results saved to: {args.output}")
        elif args.save_results:
            filepath = save_results(all_results, repo_path)
            print(f"Results saved to: {filepath}")

    finally:
        state.cleanup()


if __name__ == "__main__":
    main()
