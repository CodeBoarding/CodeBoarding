"""
Core harness for optimizing AbstractionAgent.step_clusters_grouping.

This module provides:
- ClusterGroupingSnapshot: serializable container with everything needed to replay
  step_clusters_grouping without LSP servers.
- create_snapshot(): runs static analysis + meta-agent (expensive, one-time per repo).
- run_cluster_grouping(): executes the LLM step against a snapshot (cheap, repeatable).
- score_cluster_analysis(): scores the output using production validators.

Two separate CLI commands use this module:
- prepare_snapshots.py: creates snapshots from repo paths
- run_eval.py: runs the agent against all snapshots and produces a grouped score

NOTE: Heavy project imports (agents.*, static_analyzer.*, langchain_core) are deferred
to inside functions so that OverClaw can load this module in its own environment.
"""

import logging
import pickle
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = Path("evals/overclaw_cluster_grouping/snapshots")


class ClusterGroupingSnapshot:
    """Everything needed to replay step_clusters_grouping without LSP.

    Uses Any for type hints to avoid importing project-specific types at module level.
    """

    def __init__(
        self,
        repo_dir: str,
        project_name: str,
        static_analysis: Any,
        meta_context: Any,
        cluster_results: dict[str, Any],
        expected_cluster_ids: set[int],
        total_nodes: int,
        total_clusters: int,
        languages: list[str],
        timestamp: float,
    ) -> None:
        self.repo_dir = repo_dir
        self.project_name = project_name
        self.static_analysis = static_analysis
        self.meta_context = meta_context
        self.cluster_results = cluster_results
        self.expected_cluster_ids = expected_cluster_ids
        self.total_nodes = total_nodes
        self.total_clusters = total_clusters
        self.languages = languages
        self.timestamp = timestamp


# ---------------------------------------------------------------------------
# Snapshot creation (expensive — runs LSP + meta-agent)
# ---------------------------------------------------------------------------


def create_snapshot(
    repo_path: Path,
    project_name: str | None = None,
    agent_llm: Any = None,
    parsing_llm: Any = None,
) -> ClusterGroupingSnapshot:
    """Run static analysis on a repo and create a replayable snapshot."""
    from agents.meta_agent import MetaAgent
    from static_analyzer import get_static_analysis
    from utils import generate_run_id

    if project_name is None:
        project_name = repo_path.name

    logger.info(f"Running static analysis on {repo_path}...")
    static_analysis = get_static_analysis(repo_path)

    logger.info("Building cluster results...")
    from static_analyzer.cluster_helpers import build_all_cluster_results, get_all_cluster_ids

    cluster_results = build_all_cluster_results(static_analysis)
    expected_ids = get_all_cluster_ids(cluster_results)

    total_nodes = sum(len(static_analysis.get_cfg(lang).nodes) for lang in static_analysis.get_languages())

    logger.info("Running meta-agent for project context...")
    if agent_llm is None or parsing_llm is None:
        from agents.llm_config import initialize_llms

        agent_llm, parsing_llm = initialize_llms()

    meta_agent = MetaAgent(
        repo_dir=repo_path,
        project_name=project_name,
        agent_llm=agent_llm,
        parsing_llm=parsing_llm,
        run_id=generate_run_id(),
    )
    meta_context = meta_agent.analyze_project_metadata()

    snapshot = ClusterGroupingSnapshot(
        repo_dir=str(repo_path),
        project_name=project_name,
        static_analysis=static_analysis,
        meta_context=meta_context,
        cluster_results=cluster_results,
        expected_cluster_ids=expected_ids,
        total_nodes=total_nodes,
        total_clusters=len(expected_ids),
        languages=static_analysis.get_languages(),
        timestamp=time.time(),
    )

    logger.info(
        f"Snapshot created: {len(expected_ids)} clusters, {total_nodes} nodes, " f"languages={snapshot.languages}"
    )
    return snapshot


def save_snapshot(snapshot: ClusterGroupingSnapshot, output_path: Path) -> None:
    """Pickle a snapshot for reuse."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        pickle.dump(snapshot, f)
    logger.info(f"Snapshot saved to {output_path}")


def load_snapshot(path: Path) -> ClusterGroupingSnapshot:
    """Load a pickled snapshot."""
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# Agent execution (cheap — just LLM calls)
# ---------------------------------------------------------------------------


def run_cluster_grouping(snapshot: ClusterGroupingSnapshot) -> Any:
    """Execute step_clusters_grouping using a pre-computed snapshot.

    This is the function OverClaw optimizes.
    Runs with max_validation_retries=0 so we measure raw LLM quality
    without the feedback loop inflating scores or burning tokens.
    """
    from unittest.mock import patch

    from agents.abstraction_agent import AbstractionAgent
    from agents.llm_config import initialize_llms

    agent_llm, parsing_llm = initialize_llms()

    agent = AbstractionAgent(
        repo_dir=Path(snapshot.repo_dir),
        static_analysis=snapshot.static_analysis,
        project_name=snapshot.project_name,
        meta_context=snapshot.meta_context,
        agent_llm=agent_llm,
        parsing_llm=parsing_llm,
    )

    # Wrap _validation_invoke to force max_validation_retries=0 (no retry loop)
    original = agent._validation_invoke

    def no_retry_validation_invoke(prompt, return_type, validators, context, max_validation_retries=0):
        return original(prompt, return_type, validators=validators, context=context, max_validation_retries=0)

    agent._validation_invoke = no_retry_validation_invoke  # type: ignore[assignment]
    return agent.step_clusters_grouping(snapshot.cluster_results)


# ---------------------------------------------------------------------------
# Scoring — uses production validators, returns per-dimension + composite
# ---------------------------------------------------------------------------


def _check_duplicates(result: Any) -> tuple[set[int], set[int]]:
    """Return (all_assigned_ids, duplicate_ids)."""
    seen: set[int] = set()
    duplicates: set[int] = set()
    for cc in result.cluster_components:
        for cid in cc.cluster_ids:
            if cid in seen:
                duplicates.add(cid)
            seen.add(cid)
    return seen, duplicates


def _check_hallucinated_ids(result: Any, expected: set[int]) -> set[int]:
    """Return cluster IDs in the result that don't exist in the expected set."""
    assigned: set[int] = set()
    for cc in result.cluster_components:
        assigned.update(cc.cluster_ids)
    return assigned - expected


def _check_empty_components(result: Any) -> list[str]:
    """Return names of components with no cluster_ids."""
    return [cc.name for cc in result.cluster_components if not cc.cluster_ids]


def _check_empty_descriptions(result: Any) -> list[str]:
    """Return names of components with empty or trivially short descriptions."""
    return [cc.name for cc in result.cluster_components if not cc.description or len(cc.description.strip()) < 20]


def _check_duplicate_names(result: Any) -> list[str]:
    """Return component names that appear more than once."""
    seen: dict[str, int] = {}
    for cc in result.cluster_components:
        key = cc.name.lower().strip()
        seen[key] = seen.get(key, 0) + 1
    return [name for name, count in seen.items() if count > 1]


# Weights for the composite score — shared between per-project and grouped scoring.
SCORE_WEIGHTS: dict[str, int] = {
    "coverage": 50,
    "duplicates": 15,
    "hallucinated": 10,
    "structural": 10,
    "count": 5,
    "grouping": 10,
}


def score_cluster_analysis(
    result: Any,
    snapshot: ClusterGroupingSnapshot,
) -> dict:
    """Score a ClusterAnalysis using production validators.

    Returns a dict with per-dimension scores (each 0–100), detail metrics,
    and a weighted composite total_score.
    """
    # 1. validate_cluster_coverage (production validator)
    from agents.validation import ValidationContext, validate_cluster_coverage

    context = ValidationContext(
        cluster_results=snapshot.cluster_results,
        expected_cluster_ids=snapshot.expected_cluster_ids,
    )
    coverage_validation = validate_cluster_coverage(result, context)

    assigned_ids: set[int] = set()
    for cc in result.cluster_components:
        assigned_ids.update(cc.cluster_ids)
    missing = snapshot.expected_cluster_ids - assigned_ids
    coverage_pct = len(assigned_ids & snapshot.expected_cluster_ids) / max(len(snapshot.expected_cluster_ids), 1) * 100
    coverage_score = round(coverage_pct, 2)

    # 2. Duplicate cluster IDs
    _, duplicates = _check_duplicates(result)
    duplicate_score = max(0, 100 - len(duplicates) * 10)

    # 3. Hallucinated cluster IDs
    hallucinated = _check_hallucinated_ids(result, snapshot.expected_cluster_ids)
    hallucinated_score = max(0, 100 - len(hallucinated) * 10)

    # 4. Structural quality
    empty_components = _check_empty_components(result)
    empty_descriptions = _check_empty_descriptions(result)
    duplicate_names = _check_duplicate_names(result)
    structural_issues = 0
    structural_details: list[str] = []
    if empty_components:
        structural_issues += 1
        structural_details.append(f"empty_components: {empty_components}")
    if empty_descriptions:
        structural_issues += 1
        structural_details.append(f"weak_descriptions: {empty_descriptions}")
    if duplicate_names:
        structural_issues += 1
        structural_details.append(f"duplicate_names: {duplicate_names}")
    structural_score = max(0, 100 - structural_issues * 20)

    # 5. Component count
    component_count = len(result.cluster_components)
    ideal_min, ideal_max = 4, 12
    if ideal_min <= component_count <= ideal_max:
        count_score = 100
    elif component_count < ideal_min:
        count_score = max(0, 100 - (ideal_min - component_count) * 25)
    else:
        count_score = max(0, 100 - (component_count - ideal_max) * 10)

    # 6. Grouping quality
    sizes = [len(cc.cluster_ids) for cc in result.cluster_components]
    singleton_ratio = sum(1 for s in sizes if s == 1) / max(component_count, 1)
    avg_size = sum(sizes) / max(component_count, 1)
    grouping_score = max(0, round(100 - singleton_ratio * 100, 2))

    # Weighted composite
    dim_scores = {
        "coverage": coverage_score,
        "duplicates": duplicate_score,
        "hallucinated": hallucinated_score,
        "structural": structural_score,
        "count": count_score,
        "grouping": grouping_score,
    }
    total = sum(dim_scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS) / sum(SCORE_WEIGHTS.values())

    return {
        "project_name": snapshot.project_name,
        # Primary validator
        "validate_cluster_coverage_passed": coverage_validation.is_valid,
        "validate_cluster_coverage_feedback": coverage_validation.feedback_messages,
        # Dimension scores (each 0–100)
        "coverage_score": coverage_score,
        "duplicate_score": duplicate_score,
        "hallucinated_score": hallucinated_score,
        "structural_score": structural_score,
        "count_score": count_score,
        "grouping_score": grouping_score,
        # Detail metrics
        "missing_cluster_ids": sorted(missing),
        "missing_count": len(missing),
        "duplicate_cluster_ids": sorted(duplicates),
        "duplicate_count": len(duplicates),
        "hallucinated_cluster_ids": sorted(hallucinated),
        "hallucinated_count": len(hallucinated),
        "empty_components": empty_components,
        "empty_descriptions": empty_descriptions,
        "duplicate_names": duplicate_names,
        "structural_details": structural_details,
        "component_count": component_count,
        "avg_cluster_size": round(avg_size, 2),
        "singleton_ratio": round(singleton_ratio, 2),
        # Weights
        "weights": SCORE_WEIGHTS,
        # Composite
        "total_score": round(total, 2),
    }


# ---------------------------------------------------------------------------
# OverClaw-compatible entrypoint
# ---------------------------------------------------------------------------


def run(input_data: dict) -> dict:
    """OverClaw agent entrypoint.

    Runs step_clusters_grouping against ALL snapshots in the snapshots directory,
    returns per-project results and a grouped (summed) score.
    """
    snapshot_dir = Path(input_data.get("snapshot_dir", str(SNAPSHOTS_DIR)))
    snapshot_files = sorted(snapshot_dir.glob("*.pkl"))

    if not snapshot_files:
        raise FileNotFoundError(f"No .pkl snapshots found in {snapshot_dir}")

    per_project: list[dict] = []
    for snap_path in snapshot_files:
        snapshot = load_snapshot(snap_path)
        result = run_cluster_grouping(snapshot)
        scores = score_cluster_analysis(result, snapshot)
        per_project.append(
            {
                "project_name": snapshot.project_name,
                "scores": scores,
                "cluster_analysis": result.model_dump(),
            }
        )

    # Grouped score: SUM of total_score across all projects (not averaged).
    grouped_score = sum(p["scores"]["total_score"] for p in per_project)

    return {
        "per_project": per_project,
        "grouped_score": round(grouped_score, 2),
        "project_count": len(per_project),
    }
