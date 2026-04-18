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
) -> set[str]:
    """Decide which nodes ``cfg.to_cluster_string`` should omit to fit ``char_budget``.

    Returns an empty set when the unfiltered rendering already fits. Otherwise
    returns the smallest prefix of the peel order (subject to per-cluster
    floor + global cap) whose rendering is within budget. If even the maximum
    allowed skip set does not fit, returns the best-effort set and logs a
    warning — the caller still uses it but the prompt will be oversized.
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
        logger.warning(
            "[CFG skip planner] Full rendering is %d chars (budget %d) but no peel-safe cluster members; "
            "rendering will exceed budget.",
            len(full_str),
            char_budget,
        )
        return set()

    max_skip_per_cluster = {
        cid: max(0, len(members) - min(min_keep_per_cluster, len(members)))
        for cid, members in cluster_result.clusters.items()
    }
    total_nodes = len(node_to_cluster)
    global_cap = int(total_nodes * max_peel_frac)
    allowed = _build_allowed_skip_list(peel_order, node_to_cluster, max_skip_per_cluster, global_cap)

    def render(skip: set[str]) -> int:
        return len(cfg.to_cluster_string(cluster_result=cluster_result, skip_nodes=skip))

    skip, fits = _binary_search_smallest_fit(allowed, render, char_budget)

    if fits:
        logger.info(
            "[CFG skip planner] skipping %d/%d nodes (floor=%d per cluster)",
            len(skip),
            total_nodes,
            min_keep_per_cluster,
        )
    else:
        logger.warning(
            "[CFG skip planner] Method-level filtering cannot fit budget (%d chars). "
            "Returning best-effort skip of %d/%d nodes; prompt will exceed budget.",
            char_budget,
            len(skip),
            total_nodes,
        )
    return skip
