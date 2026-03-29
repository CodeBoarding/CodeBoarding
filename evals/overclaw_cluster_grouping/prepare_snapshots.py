"""
Create static analysis snapshots for one or more repositories.

This is the expensive one-time step — it starts LSP servers, runs static analysis,
and calls the meta-agent. The resulting .pkl snapshots are reused by run_eval.py
for all optimization iterations.

Usage:
    # Single repo:
    uv run python -m evals.overclaw_cluster_grouping.prepare_snapshots \
        /Users/imilev/StartUp/repos/markitdown

    # Multiple repos:
    uv run python -m evals.overclaw_cluster_grouping.prepare_snapshots \
        /Users/imilev/StartUp/repos/markitdown \
        /Users/imilev/StartUp/repos/some-ts-project \
        /Users/imilev/StartUp/repos/another-repo

    # Custom output directory:
    uv run python -m evals.overclaw_cluster_grouping.prepare_snapshots \
        --output-dir evals/overclaw_cluster_grouping/snapshots \
        /path/to/repo1 /path/to/repo2
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from agents.llm_config import initialize_llms
from evals.overclaw_cluster_grouping.harness import (
    SNAPSHOTS_DIR,
    create_snapshot,
    save_snapshot,
)

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create static analysis snapshots for cluster grouping evaluation")
    parser.add_argument(
        "repos",
        nargs="+",
        type=str,
        help="Paths to repositories to snapshot",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(SNAPSHOTS_DIR),
        help=f"Directory to save snapshots (default: {SNAPSHOTS_DIR})",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load user config (API keys from ~/.codeboarding/config.toml) — same as main.py
    from user_config import load_user_config
    from agents.llm_config import configure_models

    user_cfg = load_user_config()
    user_cfg.apply_to_env()
    configure_models(agent_model=user_cfg.llm.agent_model, parsing_model=user_cfg.llm.parsing_model)

    # Initialize LLMs ONCE — reuse across all repos
    # Note: initialize_llms() already calls initialize_global_factory internally
    agent_llm, parsing_llm = initialize_llms()

    output_dir = Path(args.output_dir)
    results: list[dict] = []

    for repo_str in args.repos:
        repo_path = Path(repo_str).resolve()
        if not repo_path.exists():
            logger.error(f"Repo path does not exist, skipping: {repo_path}")
            continue

        project_name = repo_path.name
        output_path = output_dir / f"{project_name}.pkl"

        if output_path.exists():
            logger.info(f"Snapshot already exists: {output_path} — skipping (delete to regenerate)")
            results.append({"project": project_name, "status": "skipped", "path": str(output_path)})
            continue

        logger.info(f"\n{'='*60}\nCreating snapshot for: {project_name} ({repo_path})\n{'='*60}")
        start = time.time()

        try:
            snapshot = create_snapshot(repo_path, project_name, agent_llm, parsing_llm)
            save_snapshot(snapshot, output_path)
            elapsed = time.time() - start
            results.append(
                {
                    "project": project_name,
                    "status": "ok",
                    "path": str(output_path),
                    "clusters": snapshot.total_clusters,
                    "nodes": snapshot.total_nodes,
                    "languages": snapshot.languages,
                    "elapsed_seconds": round(elapsed, 1),
                }
            )
            logger.info(f"Done: {project_name} — {snapshot.total_clusters} clusters, {elapsed:.1f}s")
        except Exception:
            logger.exception(f"Failed to create snapshot for {project_name}")
            results.append({"project": project_name, "status": "error"})

    # Summary
    print(f"\n{'='*60}")
    print("SNAPSHOT SUMMARY")
    print(f"{'='*60}")
    ok = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]
    for r in ok:
        print(
            f"  [OK]      {r['project']:30s} {r['clusters']:>4d} clusters  {r['elapsed_seconds']:>6.1f}s  {r['path']}"
        )
    for r in skipped:
        print(f"  [SKIPPED] {r['project']:30s} {r['path']}")
    for r in errors:
        print(f"  [ERROR]   {r['project']}")
    print(f"\n{len(ok)} created, {len(skipped)} skipped, {len(errors)} errors")
    print(f"Snapshots directory: {output_dir}")
    print(f"{'='*60}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
