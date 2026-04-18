"""Plan which nodes to omit when serializing a CallGraph for LLM consumption.

The planner never mutates the graph: it returns a set of qualified names that
``CallGraph.to_cluster_string`` should drop from its output so the rendered
text fits a character budget derived from the agent's context window.

Three-stage graceful degradation when the full rendering is over budget:

Stage 1 — per-cluster floor + fraction
    Keep at least ``max(min_keep_per_cluster, ceil(size * retain_fraction))``
    methods per cluster. Small clusters stay recognizable; large clusters
    shed their lowest-degree tails first.

Stage 2 — floor only
    Drop the fraction, keep just the hard floor of ``min_keep_per_cluster``
    per cluster. Used when stage 1 can't free enough characters.

Stage 3 — give up
    Return the best-effort skip set even if it still exceeds budget.
    Caller logs a warning; the LLM or downstream truncation handles it.

Node selection follows an iterative leaf-peel order on the undirected graph,
so every skipped node is safe to remove without disconnecting the rest of
the graph. Nodes in the 2-core are never candidates.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Callable

import networkx as nx

if TYPE_CHECKING:
    from static_analyzer.graph import CallGraph, ClusterResult

logger = logging.getLogger(__name__)

# Community consensus middle ground: prose ≈ 4 chars/token, code ≈ 3 chars/token.
# 3.5 keeps us conservative without leaving too much slack.
CHARS_PER_TOKEN = 3.5


def _compute_peel_order(cfg_nx: nx.DiGraph) -> list[str]:
    """Return peel-safe nodes in iterative-leaf order.

    Repeatedly removes every node with undirected degree ≤ 1 from the live
    graph, sorted within each round by ascending degree so degree-0 isolates
    go first. Nodes that remain at the end of peeling are in the 2-core and
    are NOT returned — they are unsafe to drop.
    """
    live = cfg_nx.to_undirected()
    order: list[str] = []
    while True:
        leaves = [n for n in live.nodes if live.degree(n) <= 1]
        if not leaves:
            break
        leaves.sort(key=lambda n: live.degree(n))
        for n in leaves:
            order.append(n)
            live.remove_node(n)
    return order


def _required_keep(size: int, min_keep: int, retain_fraction: float) -> int:
    """Stage-1 minimum: ``max(min_keep, ceil(size * retain_fraction))``, clamped to size."""
    return min(size, max(min_keep, math.ceil(size * retain_fraction)))


def _stage1_max_skip_per_cluster(
    clusters: dict[int, set[str]], min_keep: int, retain_fraction: float
) -> dict[int, int]:
    return {
        cid: len(members) - _required_keep(len(members), min_keep, retain_fraction) for cid, members in clusters.items()
    }


def _stage2_max_skip_per_cluster(clusters: dict[int, set[str]], min_keep: int) -> dict[int, int]:
    return {cid: max(0, len(members) - min(min_keep, len(members))) for cid, members in clusters.items()}


def _build_allowed_skip_list(
    peel_order: list[str],
    node_to_cluster: dict[str, int],
    max_skip_per_cluster: dict[int, int],
    global_cap: int,
) -> list[str]:
    """Greedy pass over peel_order, keeping each node only if its cluster has room."""
    skipped_per_cluster: dict[int, int] = {cid: 0 for cid in max_skip_per_cluster}
    allowed: list[str] = []
    for name in peel_order:
        if len(allowed) >= global_cap:
            break
        cid = node_to_cluster.get(name)
        if cid is None:
            # Node isn't in any cluster — safe to skip (only affects unclustered edges)
            allowed.append(name)
            continue
        if skipped_per_cluster[cid] >= max_skip_per_cluster.get(cid, 0):
            continue
        allowed.append(name)
        skipped_per_cluster[cid] += 1
    return allowed


def _binary_search_smallest_fit(
    allowed: list[str],
    render: Callable[[set[str]], int],
    char_budget: int,
) -> tuple[set[str], bool]:
    """Binary-search the smallest prefix of ``allowed`` whose render fits budget.

    Returns (skip_set, fits). If even the full prefix doesn't fit, returns the
    full prefix with fits=False so the caller can decide what to do.
    """
    if not allowed:
        return set(), False

    full_skip = set(allowed)
    if render(full_skip) > char_budget:
        return full_skip, False

    lo, hi = 1, len(allowed)
    result_k = len(allowed)
    while lo <= hi:
        mid = (lo + hi) // 2
        if render(set(allowed[:mid])) <= char_budget:
            result_k = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return set(allowed[:result_k]), True


def plan_skip_set(
    cfg: CallGraph,
    cluster_result: ClusterResult,
    char_budget: int,
    max_peel_frac: float = 0.5,
    min_keep_per_cluster: int = 5,
    retain_fraction: float = 0.5,
) -> set[str]:
    """Decide which nodes ``cfg.to_cluster_string`` should omit to fit ``char_budget``.

    Returns an empty set when the unfiltered rendering already fits. Otherwise
    returns the smallest prefix of the peel order (subject to per-cluster
    constraints) whose rendering is within budget. If even the maximum allowed
    skip set does not fit, returns the best-effort set and logs a warning — the
    caller still uses it but the prompt will be oversized.
    """
    full_str = cfg.to_cluster_string(cluster_result=cluster_result)
    if len(full_str) <= char_budget:
        return set()

    cfg_nx = cfg.to_networkx()
    peel_order = _compute_peel_order(cfg_nx)

    # Restrict peel candidates to nodes that are actually rendered (i.e. members
    # of some cluster). Non-cluster nodes aren't part of the output; skipping
    # them yields no character savings.
    node_to_cluster: dict[str, int] = {
        name: cid for cid, members in cluster_result.clusters.items() for name in members
    }
    peel_order = [n for n in peel_order if n in node_to_cluster]

    if not peel_order:
        logger.warning(
            "[CFG skip planner] Full rendering is %d chars (budget %d) but no peel-safe cluster members; "
            "rendering will exceed budget.",
            len(full_str),
            char_budget,
        )
        return set()

    total_nodes = len(node_to_cluster)
    global_cap = int(total_nodes * max_peel_frac)

    def render(skip: set[str]) -> int:
        return len(cfg.to_cluster_string(cluster_result=cluster_result, skip_nodes=skip))

    stage1_caps = _stage1_max_skip_per_cluster(cluster_result.clusters, min_keep_per_cluster, retain_fraction)
    stage1_allowed = _build_allowed_skip_list(peel_order, node_to_cluster, stage1_caps, global_cap)
    stage1_skip, stage1_fits = _binary_search_smallest_fit(stage1_allowed, render, char_budget)
    if stage1_fits:
        logger.info(
            "[CFG skip planner] Stage 1 (floor=%d, retain=%.2f): skipping %d/%d nodes",
            min_keep_per_cluster,
            retain_fraction,
            len(stage1_skip),
            total_nodes,
        )
        return stage1_skip

    stage2_caps = _stage2_max_skip_per_cluster(cluster_result.clusters, min_keep_per_cluster)
    stage2_allowed = _build_allowed_skip_list(peel_order, node_to_cluster, stage2_caps, global_cap)
    stage2_skip, stage2_fits = _binary_search_smallest_fit(stage2_allowed, render, char_budget)
    if stage2_fits:
        logger.info(
            "[CFG skip planner] Stage 2 (floor=%d only): skipping %d/%d nodes",
            min_keep_per_cluster,
            len(stage2_skip),
            total_nodes,
        )
        return stage2_skip

    # Stage 3: exhausted method-level options. Return the largest allowed stage-2 set
    # (gets us as close as possible) and warn that the render will be over budget.
    logger.warning(
        "[CFG skip planner] Method-level filtering cannot fit budget (%d chars). "
        "Returning best-effort skip of %d/%d nodes; prompt will exceed budget.",
        char_budget,
        len(stage2_skip),
        total_nodes,
    )
    return stage2_skip
