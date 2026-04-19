"""Plan which nodes to omit when serializing a CallGraph for LLM consumption.

The planner never mutates the graph: it returns a set of qualified names that
``CallGraph.to_cluster_string`` should drop from its output so the rendered
text fits a character budget derived from the agent's context window.

Node selection follows an iterative leaf-peel order on the undirected graph,
so every skipped node is safe to remove without disconnecting the rest of
the graph. Nodes in the 2-core are never candidates. A per-cluster floor
keeps each cluster recognizable (at least ``min_keep_per_cluster`` members
remain rendered), and a global cap prevents extreme pruning. Binary search
over the allowed peel-order prefix picks the smallest skip set that fits.
"""

from __future__ import annotations

import logging
from typing import Callable

import networkx as nx

from static_analyzer.graph import CallGraph, ClusterResult

logger = logging.getLogger(__name__)


class ContextBudgetExceededError(RuntimeError):
    """Cluster-string render cannot fit the agent's context window.

    Raised when the pre-planning overhead already exceeds the input window,
    or when the most aggressive safe trim (per ``max_peel_frac`` and
    ``min_keep_per_cluster``) still overflows the remaining budget.
    """


def _compute_peel_order(cfg_nx: nx.DiGraph) -> list[str]:
    """Return peel-safe nodes in iterative-leaf order.

    Repeatedly removes every node with undirected degree ≤ 1 from the live
    graph, sorted within each round by ascending degree so degree-0 isolates
    go first. Nodes that remain at the end of peeling are in the 2-core and
    are NOT returned — they are unsafe to drop.
    """
    live = cfg_nx.to_undirected()
    order: list[str] = []
    while leaves := sorted(
        (n for n in live.nodes if live.degree(n) <= 1),
        key=lambda n: live.degree(n),
    ):
        for n in leaves:
            order.append(n)
            live.remove_node(n)
    return order


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
) -> set[str] | None:
    """Smallest prefix of ``allowed`` whose render fits ``char_budget``, or None if none does."""
    if not allowed or render(set(allowed)) > char_budget:
        return None

    lo, hi = 1, len(allowed)
    result_k = len(allowed)
    while lo <= hi:
        mid = (lo + hi) // 2
        if render(set(allowed[:mid])) <= char_budget:
            result_k = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return set(allowed[:result_k])


def plan_skip_set(
    cfg: CallGraph,
    cluster_result: ClusterResult,
    char_budget: int,
    max_peel_frac: float = 0.5,
    min_keep_per_cluster: int = 5,
) -> set[str]:
    """Decide which nodes ``cfg.to_cluster_string`` should omit to fit ``char_budget``.

    Returns an empty set when the unfiltered rendering already fits. Otherwise
    returns the smallest prefix of the peel order (subject to per-cluster
    floor + global cap) whose rendering is within budget.

    Raises:
        ContextBudgetExceededError: No peel-safe candidates exist, or the
            maximum allowed trim still exceeds ``char_budget``. Fail-loud is
            intentional: providers reject oversize input, so silently
            returning an over-budget render just defers the failure.
    """
    full_str = cfg.to_cluster_string(cluster_result=cluster_result)
    if len(full_str) <= char_budget:
        return set()

    cfg_nx = cfg.to_networkx()
    peel_order = _compute_peel_order(cfg_nx)

    # Restrict peel candidates to nodes that are actually rendered (cluster
    # members). Non-cluster nodes yield no character savings.
    node_to_cluster: dict[str, int] = {
        name: cid for cid, members in cluster_result.clusters.items() for name in members
    }
    peel_order = [n for n in peel_order if n in node_to_cluster]

    if not peel_order:
        msg = (
            f"No peel-safe cluster members to prune; full render "
            f"{len(full_str)} chars exceeds budget {char_budget}."
        )
        logger.error("[CFG skip planner] %s", msg)
        raise ContextBudgetExceededError(msg)

    max_skip_per_cluster = {
        cid: max(0, len(members) - min(min_keep_per_cluster, len(members)))
        for cid, members in cluster_result.clusters.items()
    }
    total_nodes = len(node_to_cluster)
    global_cap = int(total_nodes * max_peel_frac)
    allowed = _build_allowed_skip_list(peel_order, node_to_cluster, max_skip_per_cluster, global_cap)

    def render(skip: set[str]) -> int:
        return len(cfg.to_cluster_string(cluster_result=cluster_result, skip_nodes=skip))

    skip = _binary_search_smallest_fit(allowed, render, char_budget)

    if skip is None:
        msg = (
            f"No allowed skip prefix fits budget {char_budget} chars "
            f"(full render {len(full_str)}, {len(allowed)}/{total_nodes} nodes prunable)."
        )
        logger.error("[CFG skip planner] %s", msg)
        raise ContextBudgetExceededError(msg)

    logger.info(
        "[CFG skip planner] skipping %d/%d nodes (floor=%d per cluster)",
        len(skip),
        total_nodes,
        min_keep_per_cluster,
    )
    return skip
