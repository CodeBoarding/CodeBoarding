"""
Run cluster grouping evaluation across all project snapshots.

Executes step_clusters_grouping against every .pkl snapshot in the snapshots
directory, scores each, and produces a GROUPED score (summed, not averaged)
so that improvements must generalize across all projects.

Usage:
    # Prepare snapshots first (one-time):
    uv run python -m evals.overclaw_cluster_grouping.prepare_snapshots \
        /path/to/repo1 /path/to/repo2 /path/to/repo3

    # Run evaluation:
    uv run python -m evals.overclaw_cluster_grouping.run_eval

    # Custom snapshot dir + output:
    uv run python -m evals.overclaw_cluster_grouping.run_eval \
        --snapshot-dir evals/overclaw_cluster_grouping/snapshots \
        --output evals/overclaw_cluster_grouping/results.json
"""

import argparse
import json
import logging
import time
from pathlib import Path

from agents.llm_config import initialize_llms
from evals.overclaw_cluster_grouping.harness import (
    SCORE_WEIGHTS,
    SNAPSHOTS_DIR,
    load_snapshot,
    run_cluster_grouping,
    score_cluster_analysis,
)

logger = logging.getLogger(__name__)


def run_evaluation(snapshot_dir: Path, output_path: Path | None = None) -> dict:
    """Run cluster grouping against all snapshots and produce a grouped score."""
    snapshot_files = sorted(snapshot_dir.glob("*.pkl"))
    if not snapshot_files:
        logger.error(f"No .pkl snapshots found in {snapshot_dir}")
        raise FileNotFoundError(f"No .pkl snapshots found in {snapshot_dir}")

    logger.info(f"Found {len(snapshot_files)} snapshot(s) in {snapshot_dir}")

    per_project: list[dict] = []

    for snap_path in snapshot_files:
        snapshot = load_snapshot(snap_path)
        logger.info(
            f"\n{'='*60}\n"
            f"Project: {snapshot.project_name} "
            f"({snapshot.total_clusters} clusters, {snapshot.total_nodes} nodes)\n"
            f"{'='*60}"
        )

        start = time.time()
        result = run_cluster_grouping(snapshot)
        elapsed = time.time() - start

        scores = score_cluster_analysis(result, snapshot)
        scores["elapsed_seconds"] = round(elapsed, 2)

        logger.info(
            f"{snapshot.project_name}: total={scores['total_score']}, "
            f"coverage={scores['coverage_score']}%, "
            f"missing={scores['missing_count']}, "
            f"duplicates={scores['duplicate_count']}, "
            f"hallucinated={scores['hallucinated_count']}, "
            f"structural={scores['structural_score']}, "
            f"components={scores['component_count']}, "
            f"elapsed={elapsed:.1f}s"
        )

        per_project.append(
            {
                "project_name": snapshot.project_name,
                "total_clusters": snapshot.total_clusters,
                "total_nodes": snapshot.total_nodes,
                "languages": snapshot.languages,
                "scores": scores,
                "component_names": [cc.name for cc in result.cluster_components],
                "result": result.model_dump(),
            }
        )

    # --- Grouped score: SUM across all projects (not averaged) ---
    grouped_total = sum(p["scores"]["total_score"] for p in per_project)

    # Also sum each dimension separately for visibility
    grouped_dimensions: dict[str, float] = {}
    dim_to_key = {
        "coverage": "coverage_score",
        "duplicates": "duplicate_score",
        "hallucinated": "hallucinated_score",
        "structural": "structural_score",
        "count": "count_score",
        "grouping": "grouping_score",
    }
    for dim, key in dim_to_key.items():
        grouped_dimensions[dim] = round(sum(p["scores"][key] for p in per_project), 2)

    # Max possible = 100 * number_of_projects
    max_possible = 100.0 * len(per_project)

    summary = {
        "project_count": len(per_project),
        "max_possible_score": max_possible,
        "grouped_score": round(grouped_total, 2),
        "grouped_score_pct": round(grouped_total / max_possible * 100, 2) if max_possible > 0 else 0,
        "grouped_dimensions": grouped_dimensions,
        "weights": SCORE_WEIGHTS,
        "per_project": [
            {
                "project_name": p["project_name"],
                "total_clusters": p["total_clusters"],
                "total_nodes": p["total_nodes"],
                "languages": p["languages"],
                "total_score": p["scores"]["total_score"],
                "coverage_score": p["scores"]["coverage_score"],
                "validate_cluster_coverage_passed": p["scores"]["validate_cluster_coverage_passed"],
                "missing_count": p["scores"]["missing_count"],
                "duplicate_count": p["scores"]["duplicate_count"],
                "hallucinated_count": p["scores"]["hallucinated_count"],
                "structural_score": p["scores"]["structural_score"],
                "component_count": p["scores"]["component_count"],
                "singleton_ratio": p["scores"]["singleton_ratio"],
                "component_names": p["component_names"],
                "elapsed_seconds": p["scores"]["elapsed_seconds"],
            }
            for p in per_project
        ],
    }

    # Print
    print(f"\n{'='*70}")
    print(f"GROUPED EVALUATION — {len(per_project)} project(s)")
    print(f"{'='*70}")
    print()

    for p in per_project:
        s = p["scores"]
        passed = "PASS" if s["validate_cluster_coverage_passed"] else "FAIL"
        print(
            f"  {p['project_name']:25s}  "
            f"score={s['total_score']:6.2f}  "
            f"coverage={s['coverage_score']:6.2f}%  "
            f"[{passed}]  "
            f"missing={s['missing_count']}  "
            f"dup={s['duplicate_count']}  "
            f"hall={s['hallucinated_count']}  "
            f"components={s['component_count']}  "
            f"{s['elapsed_seconds']:.1f}s"
        )

    print()
    print(
        f"  {'GROUPED SCORE':25s}  {grouped_total:.2f} / {max_possible:.0f}  " f"({summary['grouped_score_pct']:.1f}%)"
    )
    print()

    # Dimension breakdown
    print(f"  Dimension breakdown (summed across projects):")
    for dim, val in grouped_dimensions.items():
        dim_max = SCORE_WEIGHTS[dim] / sum(SCORE_WEIGHTS.values()) * max_possible
        print(f"    {dim:15s}  {val:7.2f}  (weight {SCORE_WEIGHTS[dim]})")

    print(f"{'='*70}")

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f"Results saved to {output_path}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run cluster grouping evaluation across all project snapshots")
    parser.add_argument(
        "--snapshot-dir",
        type=str,
        default=str(SNAPSHOTS_DIR),
        help=f"Directory containing .pkl snapshots (default: {SNAPSHOTS_DIR})",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output path for results JSON",
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

    # Note: initialize_llms() already calls initialize_global_factory internally
    agent_llm, _ = initialize_llms()

    output_path = Path(args.output) if args.output else None
    run_evaluation(Path(args.snapshot_dir), output_path)


if __name__ == "__main__":
    main()
