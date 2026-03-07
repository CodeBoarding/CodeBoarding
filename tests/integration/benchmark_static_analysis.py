"""Benchmark script for comparing static analysis performance between two CodeBoarding commits.

Usage:
    # Compare two commits on mockito_java with 3 iterations each
    uv run python tests/integration/benchmark_static_analysis.py d813a36 6b9d951 --repo mockito_java --iterations 3

    # Compare using an already-cloned target repo
    uv run python tests/integration/benchmark_static_analysis.py HEAD HEAD~1 --repo mockito_java --repo-path /tmp/mockito

    # Run all configured repos (1 iteration each for quick check)
    uv run python tests/integration/benchmark_static_analysis.py HEAD HEAD~1 --iterations 1

Worker mode (called by main process, not intended for direct use):
    uv run python tests/integration/benchmark_static_analysis.py --worker --repo mockito_java --repo-path /tmp/mockito --cb-root /tmp/cb_clone
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

CODEBOARDING_ROOT = Path(__file__).parent.parent.parent.resolve()
CODEBOARDING_REPO_URL = "https://github.com/CodeBoarding/CodeBoarding.git"
BENCHMARK_RESULTS_DIR = CODEBOARDING_ROOT / "benchmark_results"

# Repository configs inlined to avoid import issues when running from a different clone
REPO_CONFIGS: dict[str, dict] = {
    "codeboarding_python": {
        "repo_url": "https://github.com/CodeBoarding/CodeBoarding",
        "pinned_commit": "03b25afe8d37ce733e5f70c3cbcdfb52f4883dcd",
        "language": "Python",
        "mock_language": {
            "language": "Python",
            "size": 50000,
            "percentage": 100.0,
            "suffixes": [".py"],
            "server_commands": ["pyright-langserver", "--stdio"],
            "lsp_server_key": "python",
        },
    },
    "mockito_java": {
        "repo_url": "https://github.com/mockito/mockito",
        "pinned_commit": "v5.14.2",
        "language": "Java",
        "mock_language": {
            "language": "Java",
            "size": 100000,
            "percentage": 100.0,
            "suffixes": [".java"],
            "server_commands": ["java"],
            "lsp_server_key": "java",
        },
    },
    "prometheus_go": {
        "repo_url": "https://github.com/prometheus/prometheus",
        "pinned_commit": "v3.0.1",
        "language": "Go",
        "mock_language": {
            "language": "Go",
            "size": 200000,
            "percentage": 100.0,
            "suffixes": [".go"],
            "server_commands": ["gopls", "serve"],
            "lsp_server_key": "go",
        },
    },
    "excalidraw_typescript": {
        "repo_url": "https://github.com/excalidraw/excalidraw",
        "pinned_commit": "v0.18.0",
        "language": "TypeScript",
        "mock_language": {
            "language": "TypeScript",
            "size": 150000,
            "percentage": 100.0,
            "suffixes": [".ts", ".tsx"],
            "server_commands": ["typescript-language-server", "--stdio"],
            "lsp_server_key": "typescript",
        },
    },
    "wordpress_php": {
        "repo_url": "https://github.com/WordPress/WordPress",
        "pinned_commit": "6.7",
        "language": "PHP",
        "mock_language": {
            "language": "PHP",
            "size": 300000,
            "percentage": 100.0,
            "suffixes": [".php"],
            "server_commands": ["intelephense", "--stdio"],
            "lsp_server_key": "php",
        },
    },
    "lodash_javascript": {
        "repo_url": "https://github.com/lodash/lodash",
        "pinned_commit": "4.17.21",
        "language": "JavaScript",
        "mock_language": {
            "language": "JavaScript",
            "size": 100000,
            "percentage": 100.0,
            "suffixes": [".js", ".mjs"],
            "server_commands": ["typescript-language-server", "--stdio"],
            "lsp_server_key": "typescript",
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark static analysis across CodeBoarding commits")
    parser.add_argument("commit_a", nargs="?", help="First commit hash/tag to benchmark")
    parser.add_argument("commit_b", nargs="?", help="Second commit hash/tag to benchmark")
    parser.add_argument("--repo", help="Repository config name (e.g. mockito_java). Default: all repos")
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of iterations per commit (default: 3)",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        help="Path to already-cloned target repo (requires --repo)",
    )
    parser.add_argument(
        "--binary-location",
        type=Path,
        help="Path to binary directory for LSP servers (JDTLS, etc.)",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cb-root", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_commit(ref: str) -> str:
    return run_git(["rev-parse", "--short", ref], cwd=CODEBOARDING_ROOT)


def clone_target_repo(repo_name: str, config: dict, work_dir: Path) -> Path:
    repo_dir = work_dir / repo_name
    if repo_dir.exists():
        print(f"  Reusing existing clone at {repo_dir}")
        return repo_dir
    pinned = config["pinned_commit"]
    print(f"  Cloning {config['repo_url']} at {pinned}...")
    # Try shallow clone with --branch (works for tags/branches)
    result = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            pinned,
            config["repo_url"],
            str(repo_dir),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Pinned commit is a hash — need full clone then checkout
        print(f"  Shallow clone failed, doing full clone + checkout...")
        subprocess.run(
            ["git", "clone", config["repo_url"], str(repo_dir)],
            capture_output=True,
            text=True,
            check=True,
        )
        run_git(["checkout", pinned], cwd=repo_dir)
    return repo_dir


def clone_codeboarding(work_dir: Path) -> Path:
    """Clone CodeBoarding repo once into work_dir. We'll checkout commits inside it."""
    cb_dir = work_dir / "codeboarding_clone"
    if cb_dir.exists():
        return cb_dir
    print(f"Cloning CodeBoarding into {cb_dir}...")
    subprocess.run(
        ["git", "clone", str(CODEBOARDING_ROOT), str(cb_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    # Copy config files that aren't in git
    for config_file in [".env", "static_analysis_config.yml"]:
        src = CODEBOARDING_ROOT / config_file
        if src.exists():
            shutil.copy2(src, cb_dir / config_file)
    # Install dependencies in the clone
    print("Installing dependencies in clone...")
    result = subprocess.run(["uv", "sync"], cwd=cb_dir, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Warning: uv sync failed: {result.stderr[:500]}", file=sys.stderr)
    return cb_dir


def run_worker(cb_root: Path, repo_path: Path, repo_name: str, binary_location: Path | None = None) -> dict:
    """Run a single analysis iteration in a subprocess using the cloned CodeBoarding."""
    cmd = [
        "uv",
        "run",
        "python",
        str(cb_root / "benchmark_static_analysis.py"),
        "--worker",
        "--repo",
        repo_name,
        "--repo-path",
        str(repo_path),
        "--cb-root",
        str(cb_root),
    ]
    if binary_location:
        cmd.extend(["--binary-location", str(binary_location)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=str(cb_root))
    if result.returncode != 0:
        print(f"  Worker stderr:\n{result.stderr[-2000:]}", file=sys.stderr)
        raise RuntimeError(f"Worker failed with exit code {result.returncode}")
    # Find the JSON line in stdout (last line starting with '{')
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"Worker did not produce JSON output. stdout:\n{result.stdout[-2000:]}")


def do_worker(repo_path: Path, repo_name: str) -> None:
    """Worker mode: run a single analysis and print JSON result to stdout."""
    from unittest.mock import patch

    from static_analyzer import StaticAnalyzer
    from static_analyzer.lsp_client.java_client import JavaClient
    from static_analyzer.programming_language import JavaConfig, ProgrammingLanguage
    from static_analyzer.scanner import ProjectScanner
    from utils import get_config

    config = REPO_CONFIGS[repo_name]
    mock_lang: dict = config["mock_language"]

    # Build ProgrammingLanguage with real LSP server config
    lsp_servers = get_config("lsp_servers")
    lsp_key: str = mock_lang["lsp_server_key"]
    server_commands: list[str] = mock_lang["server_commands"]
    language_specific_config = None

    if lsp_key in lsp_servers:
        lsp_config = lsp_servers[lsp_key]
        server_commands = lsp_config.get("command", server_commands)
        if lsp_key == "java" and "jdtls_root" in lsp_config:
            language_specific_config = JavaConfig(jdtls_root=Path(lsp_config["jdtls_root"]))

    lang_name: str = mock_lang["language"]
    lang_size: int = mock_lang["size"]
    lang_pct: float = mock_lang["percentage"]
    lang_suffixes: list[str] = mock_lang["suffixes"]

    def mock_scan(self) -> list:
        return [
            ProgrammingLanguage(
                language=lang_name,
                size=lang_size,
                percentage=lang_pct,
                suffixes=lang_suffixes,
                server_commands=server_commands,
                lsp_server_key=lsp_key,
                language_specific_config=language_specific_config,
            )
        ]

    wall_start = time.perf_counter()

    with patch.object(ProjectScanner, "scan", mock_scan):
        analyzer = StaticAnalyzer(repo_path)

    for client in analyzer.clients:
        client.start()
        if isinstance(client, JavaClient):
            client.wait_for_import()

        analysis = client.build_static_analysis()
        wall_elapsed = time.perf_counter() - wall_start

        file_results = analysis.get("file_analysis_results", [])
        call_graph = analysis["call_graph"]

        # Extract timing
        hierarchy_time = sum(r.method_timings.get("hierarchy", 0) for r in file_results)
        outgoing_time = sum(r.method_timings.get("outgoing", 0) for r in file_results)
        incoming_time = sum(r.method_timings.get("incoming", 0) for r in file_results)
        body_time = sum(r.method_timings.get("body", 0) for r in file_results)

        # Extract edge counts
        outgoing_edges = set(e for r in file_results for e in r.outgoing_edges)
        incoming_edges = set(e for r in file_results for e in r.incoming_edges)
        body_edges = set(e for r in file_results for e in r.body_edges)

        result = {
            "wall_clock": round(wall_elapsed, 2),
            "hierarchy_time": round(hierarchy_time, 2),
            "outgoing_time": round(outgoing_time, 2),
            "incoming_time": round(incoming_time, 2),
            "body_time": round(body_time, 2),
            "cumulative_lsp": round(hierarchy_time + outgoing_time + incoming_time + body_time, 2),
            "call_graph_nodes": len(call_graph.nodes),
            "call_graph_edges": len(call_graph.edges),
            "outgoing_edges": len(outgoing_edges),
            "incoming_edges": len(incoming_edges),
            "body_edges": len(body_edges),
            "files_analyzed": len(file_results),
        }

        client.close()
        print(json.dumps(result))
        return

    print(json.dumps({"error": "No clients created"}))


def compute_averages(results: list[dict]) -> dict:
    if not results:
        return {}
    avg = {}
    for key in results[0]:
        values = [r[key] for r in results]
        avg[key] = round(sum(values) / len(values), 2)
    return avg


def save_results(repo_name: str, commit_a: str, commit_b: str, all_a: list[dict], all_b: list[dict]) -> Path:
    """Save benchmark results to JSON file."""
    BENCHMARK_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{repo_name}_{commit_a}_{commit_b}_{timestamp}.json"
    filepath = BENCHMARK_RESULTS_DIR / filename

    data = {
        "repo": repo_name,
        "timestamp": timestamp,
        "commits": {"a": commit_a, "b": commit_b},
        "iterations_a": all_a,
        "iterations_b": all_b,
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    return filepath


def print_comparison(
    name: str,
    avg_a: dict,
    avg_b: dict,
    commit_a: str,
    commit_b: str,
    all_a: list[dict],
    all_b: list[dict],
    iterations: int,
) -> None:
    print(f"\n{'=' * 70}")
    print(f"  Benchmark: {name} ({iterations} iterations)")
    print(f"{'=' * 70}\n")

    # Per-iteration wall clock
    print("  Per-iteration wall clock (seconds):")
    print(f"    {commit_a}: {[r['wall_clock'] for r in all_a]}")
    print(f"    {commit_b}: {[r['wall_clock'] for r in all_b]}")
    print()

    # Comparison table
    metrics = [
        ("wall_clock", "Wall clock avg", "s"),
        ("hierarchy_time", "Hierarchy time", "s"),
        ("outgoing_time", "Outgoing time", "s"),
        ("incoming_time", "Incoming time", "s"),
        ("body_time", "Body time", "s"),
        ("cumulative_lsp", "Cumulative LSP", "s"),
        ("call_graph_nodes", "Call graph nodes", ""),
        ("call_graph_edges", "Call graph edges", ""),
        ("outgoing_edges", "Outgoing edges", ""),
        ("incoming_edges", "Incoming edges", ""),
        ("body_edges", "Body edges", ""),
        ("files_analyzed", "Files analyzed", ""),
    ]

    header = f"  {'Metric':<25} {commit_a:>15} {commit_b:>15} {'Delta':>10}"
    print(header)
    print(f"  {'-' * 65}")

    for key, label, unit in metrics:
        val_a = avg_a.get(key, 0)
        val_b = avg_b.get(key, 0)
        if val_a != 0:
            delta_pct = ((val_b - val_a) / val_a) * 100
            delta_str = f"{delta_pct:+.1f}%"
        else:
            delta_str = "N/A"

        suffix = unit
        print(f"  {label:<25} {val_a:>14.1f}{suffix} {val_b:>14.1f}{suffix} {delta_str:>10}")

    print()


def main() -> None:
    args = parse_args()

    if args.worker:
        if not args.repo or not args.repo_path:
            print("Worker mode requires --repo and --repo-path", file=sys.stderr)
            sys.exit(1)
        if args.binary_location:
            from vscode_constants import update_config

            update_config(args.binary_location)
        do_worker(args.repo_path.resolve(), args.repo)
        return

    if not args.commit_a or not args.commit_b:
        print(
            "Usage: benchmark_static_analysis.py <commit_a> <commit_b> [options]",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.repo_path and not args.repo:
        print("--repo-path requires --repo", file=sys.stderr)
        sys.exit(1)

    # Resolve commits to full hashes (for checkout in the clone)
    commit_a_full = run_git(["rev-parse", args.commit_a], cwd=CODEBOARDING_ROOT)
    commit_b_full = run_git(["rev-parse", args.commit_b], cwd=CODEBOARDING_ROOT)
    commit_a_short = commit_a_full[:7]
    commit_b_short = commit_b_full[:7]

    # Select repos
    if args.repo:
        if args.repo not in REPO_CONFIGS:
            print(
                f"Unknown repo: {args.repo}. Available: {', '.join(REPO_CONFIGS.keys())}",
                file=sys.stderr,
            )
            sys.exit(1)
        selected = {args.repo: REPO_CONFIGS[args.repo]}
    else:
        selected = REPO_CONFIGS

    print(f"Benchmark: comparing {commit_a_short} vs {commit_b_short}")
    print(f"Repos: {', '.join(selected.keys())}")
    print(f"Iterations: {args.iterations}")
    print()

    with tempfile.TemporaryDirectory(prefix="codeboarding_bench_") as tmp_dir:
        work_dir = Path(tmp_dir)

        # Clone CodeBoarding once — we'll checkout different commits inside it
        cb_clone = clone_codeboarding(work_dir)

        # Copy this benchmark script into the clone so the worker can find it
        shutil.copy2(Path(__file__), cb_clone / "benchmark_static_analysis.py")

        for repo_name, config in selected.items():
            # Clone target repo (or use provided path)
            if args.repo_path:
                repo_path = args.repo_path.resolve()
            else:
                repo_path = clone_target_repo(repo_name, config, work_dir)

            all_results: dict[str, list[dict]] = {}

            for commit_full, commit_short in [
                (commit_a_full, commit_a_short),
                (commit_b_full, commit_b_short),
            ]:
                print(f"\n--- {repo_name}: benchmarking commit {commit_short} ---")
                run_git(["checkout", commit_full], cwd=cb_clone)

                results = []
                for i in range(args.iterations):
                    print(f"  Iteration {i + 1}/{args.iterations}...", end=" ", flush=True)
                    try:
                        r = run_worker(cb_clone, repo_path, repo_name, args.binary_location)
                        if "error" in r:
                            print(f"ERROR: {r['error']}")
                        else:
                            print(f"wall_clock={r['wall_clock']}s, edges={r['call_graph_edges']}")
                            results.append(r)
                    except Exception as e:
                        print(f"FAILED: {e}")

                all_results[commit_short] = results

            # Print comparison and save results
            if all_results.get(commit_a_short) and all_results.get(commit_b_short):
                avg_a = compute_averages(all_results[commit_a_short])
                avg_b = compute_averages(all_results[commit_b_short])
                print_comparison(
                    repo_name,
                    avg_a,
                    avg_b,
                    commit_a_short,
                    commit_b_short,
                    all_results[commit_a_short],
                    all_results[commit_b_short],
                    args.iterations,
                )
                results_file = save_results(
                    repo_name,
                    commit_a_short,
                    commit_b_short,
                    all_results[commit_a_short],
                    all_results[commit_b_short],
                )
                print(f"  Results saved to: {results_file}")
            else:
                print(f"\n  Insufficient results for {repo_name} to compare.")


if __name__ == "__main__":
    main()
