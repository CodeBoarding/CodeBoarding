"""
Generate an OverClaw-compatible dataset.json from prepared snapshots.

Each test case is one project. The input is the snapshot path, and the
expected_output describes what a perfect cluster grouping looks like
(all cluster IDs assigned, no duplicates, no hallucinations).

Usage:
    uv run python -m evals.overclaw_cluster_grouping.generate_dataset
"""

import json
import logging
from pathlib import Path

from evals.overclaw_cluster_grouping.harness import SNAPSHOTS_DIR, load_snapshot

logger = logging.getLogger(__name__)

# Minimum clusters to be a meaningful test case (skip empty graphs like vitest)
MIN_CLUSTERS = 5


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    snapshot_files = sorted(SNAPSHOTS_DIR.glob("*.pkl"))
    if not snapshot_files:
        logger.error(f"No snapshots found in {SNAPSHOTS_DIR}")
        return

    dataset: list[dict] = []

    for snap_path in snapshot_files:
        snapshot = load_snapshot(snap_path)

        if snapshot.total_clusters < MIN_CLUSTERS:
            logger.info(
                f"Skipping {snapshot.project_name}: only {snapshot.total_clusters} clusters (min={MIN_CLUSTERS})"
            )
            continue

        # The input OverClaw passes to run()
        test_input = {
            "snapshot_dir": str(SNAPSHOTS_DIR),
        }

        # What a perfect result looks like for this project
        expected_output = {
            "project_name": snapshot.project_name,
            "all_cluster_ids_assigned": True,
            "expected_cluster_ids": sorted(snapshot.expected_cluster_ids),
            "total_clusters": snapshot.total_clusters,
            "duplicate_count": 0,
            "hallucinated_count": 0,
            "missing_count": 0,
            "component_count_range": [4, 12],
            "singleton_ratio_max": 0.3,
            "coverage_score": 100.0,
            "total_score_min": 90.0,
        }

        dataset.append(
            {
                "input": test_input,
                "expected_output": expected_output,
            }
        )

        logger.info(
            f"Added {snapshot.project_name}: {snapshot.total_clusters} clusters, "
            f"{snapshot.total_nodes} nodes, languages={snapshot.languages}"
        )

    # Write dataset
    output_path = Path(".overclaw/agents/cluster_grouping/setup_spec/dataset.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(dataset, f, indent=2)

    # Also write a copy in our evals dir
    local_copy = SNAPSHOTS_DIR.parent / "dataset.json"
    with open(local_copy, "w") as f:
        json.dump(dataset, f, indent=2)

    logger.info(f"Dataset with {len(dataset)} test cases written to {output_path}")
    logger.info(f"Local copy at {local_copy}")

    # Print summary
    print(f"\nDataset: {len(dataset)} test cases")
    for entry in dataset:
        eo = entry["expected_output"]
        print(f"  {eo['project_name']:20s}  {eo['total_clusters']:>4d} clusters")
    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()
