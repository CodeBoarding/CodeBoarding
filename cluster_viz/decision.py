"""Replay the deterministic grouping decision that turned leaf clusters into components.

``supercluster_by_modularity_peak`` only returns the winning partition, so the
reasoning behind it — which resolution won the sweep, which communities became
seeds, where every leftover cluster was absorbed and why — is not recoverable
from the artifacts alone. Everything it does is deterministic (fixed seed, fixed
resolution ladder), so this module re-runs it against the recorded clusters with
the intermediate steps recorded, and checks the replayed grouping against the
real entry point so a drifting mirror shows up as a failed check rather than a
plausible-looking lie.

It deliberately reaches for the private helpers of ``cluster_helpers``: the
point is to reproduce that module's decision exactly, not to re-derive it.
"""

import networkx as nx
import networkx.algorithms.community as nx_comm
from dataclasses import dataclass, field

from static_analyzer.cluster_helpers import (
    _RESOLUTION_LADDER,
    _build_meta_graph,
    _cluster_packages,
    _method_counts,
    _package_affinity,
    _seed_distances,
    _seeds_from_partition,
    supercluster_by_modularity_peak,
)
from static_analyzer.constants import ClusteringConfig
from static_analyzer.graph import ClusterResult, detect_communities


@dataclass
class SweepEntry:
    """One rung of the resolution ladder and the partition it produced."""

    resolution: float
    communities: int
    non_singleton: int
    modularity: float
    in_range: bool
    chosen: bool


@dataclass
class Absorption:
    """Where a leftover leaf cluster ended up, and what put it there."""

    cluster_id: int
    group_index: int
    reason: str
    hops: int
    package_affinity: int


@dataclass
class GroupingDecision:
    """The full trace of one leaf-clusters-to-components grouping."""

    low: int
    high: int
    leaf_clusters: int
    sweep: list[SweepEntry] = field(default_factory=list)
    #: Non-singleton communities the sweep kept, before any absorption.
    seeds: list[list[int]] = field(default_factory=list)
    #: Leaf clusters promoted to their own seed to reach the ``low`` floor.
    promoted: list[int] = field(default_factory=list)
    absorptions: list[Absorption] = field(default_factory=list)
    groups: list[list[int]] = field(default_factory=list)
    modularity: float = 0.0
    #: False when the replay stopped reproducing ``supercluster_by_modularity_peak``.
    matches_pipeline: bool = True
    note: str = ""


def modularity_of(meta_graph: nx.DiGraph, groups: list[set[int]]) -> float:
    """Weighted modularity of a grouping; 0.0 when the meta-graph has no edges."""
    if not meta_graph.number_of_edges() or not groups:
        return 0.0
    return float(nx_comm.modularity(meta_graph, groups, weight="weight"))


def _sweep(meta_graph: nx.DiGraph, low: int, high: int, seed: int) -> tuple[list[SweepEntry], list[set[int]]]:
    """Mirror ``_pick_peak_partition``, keeping every candidate it scored."""
    entries: list[SweepEntry] = []
    candidates: list[tuple[int, int, float, list[set[int]]]] = []
    for resolution in _RESOLUTION_LADDER:
        try:
            communities: list[set[int]] = detect_communities(
                meta_graph, weight="weight", resolution=resolution, seed=seed
            )
        except Exception:  # noqa: BLE001 - mirrors _pick_peak_partition, which skips a failed rung
            continue
        non_singleton = sum(1 for community in communities if len(community) >= 2)
        modularity = float(nx_comm.modularity(meta_graph, communities, weight="weight"))
        candidates.append((len(entries), non_singleton, modularity, communities))
        entries.append(
            SweepEntry(
                resolution=resolution,
                communities=len(communities),
                non_singleton=non_singleton,
                modularity=modularity,
                in_range=low <= non_singleton <= high,
                chosen=False,
            )
        )

    if not candidates:
        return entries, [{cid} for cid in meta_graph.nodes]

    in_range = [candidate for candidate in candidates if low <= candidate[1] <= high]
    if in_range:
        winner = max(in_range, key=lambda candidate: candidate[2])
    else:
        winner = min(
            candidates,
            key=lambda candidate: (
                0 if low <= candidate[1] <= high else min(abs(candidate[1] - low), abs(candidate[1] - high)),
                -candidate[2],
            ),
        )
    entries[winner[0]].chosen = True
    return entries, winner[3]


def _absorb(
    seeds: list[set[int]],
    leftovers: list[int],
    meta_graph: nx.DiGraph,
    cluster_result: ClusterResult,
    method_count: dict[int, int],
) -> list[Absorption]:
    """Mirror ``_absorb_leftovers``, recording the winning criterion per leftover."""
    distances = _seed_distances(meta_graph, seeds)
    seed_packages = [{pkg for cid in seed for pkg in _cluster_packages(cid, cluster_result)} for seed in seeds]
    sizes = [sum(method_count.get(cid, 0) for cid in seed) for seed in seeds]
    absorptions: list[Absorption] = []

    for cid in leftovers:
        reason = "hops"
        affinity_by_seed: dict[int, int] = {}
        ranked = [(reached[cid], sizes[idx], idx) for idx, reached in enumerate(distances) if cid in reached]
        if not ranked:
            reason = "package"
            packages = _cluster_packages(cid, cluster_result)
            for idx, seed_pkgs in enumerate(seed_packages):
                affinity = max((_package_affinity(pkg, seed_pkgs) for pkg in packages), default=0)
                affinity_by_seed[idx] = affinity
                if affinity:
                    ranked.append((-affinity, sizes[idx], idx))
        if not ranked:
            reason = "smallest_group"
            ranked = [(0, sizes[idx], idx) for idx in range(len(seeds))]

        rank, _size, target = min(ranked)
        seeds[target].add(cid)
        sizes[target] += method_count.get(cid, 0)
        absorptions.append(
            Absorption(
                cluster_id=cid,
                group_index=target,
                reason=reason,
                hops=rank if reason == "hops" else -1,
                package_affinity=affinity_by_seed.get(target, 0),
            )
        )
    return absorptions


def replay_grouping(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    low: int,
    high: int,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> tuple[GroupingDecision, nx.DiGraph]:
    """Re-derive one scope's grouping with its intermediate steps, plus the meta-graph."""
    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    n_leaf = meta_graph.number_of_nodes()
    decision = GroupingDecision(low=low, high=high, leaf_clusters=n_leaf)

    if n_leaf == 0:
        decision.note = "no leaf clusters"
        return decision, meta_graph

    if n_leaf <= low:
        # ``_optimize_grouping`` stops here too: fewer clusters than the floor, one each.
        decision.groups = [[cid] for cid in meta_graph.nodes]
        decision.note = f"{n_leaf} leaf clusters at or below the floor of {low}; each is its own component"
    else:
        high = min(high, n_leaf)
        decision.high = high
        method_count = _method_counts(cluster_result)
        if meta_graph.number_of_edges() == 0:
            # ``_pick_peak_partition`` returns all-singletons without sweeping; the grouping
            # that follows therefore comes entirely from promotion and absorption, with no
            # call-graph evidence behind it at all.
            communities = [{cid} for cid in meta_graph.nodes]
            decision.note = (
                "no edges between the leaf clusters, so no resolution was swept: the components "
                "below come from promoting the largest clusters to the floor and balancing the rest by size"
            )
        else:
            decision.sweep, communities = _sweep(meta_graph, low, high, seed)

        seeds, leftovers = _seeds_from_partition(communities, method_count, low, high)
        non_singleton = min(sum(1 for community in communities if len(community) >= 2), high)
        decision.promoted = [cid for group in seeds[non_singleton:] for cid in group]
        decision.seeds = [sorted(group) for group in seeds]
        if seeds:
            decision.absorptions = _absorb(seeds, leftovers, meta_graph, cluster_result, method_count)
        decision.groups = [sorted(group) for group in seeds]
        decision.modularity = modularity_of(meta_graph, seeds)

    pipeline_groups, _ = supercluster_by_modularity_peak(cluster_result, cfg_graph, low, high, seed)
    decision.matches_pipeline = sorted(sorted(group) for group in pipeline_groups) == sorted(decision.groups)
    if not decision.matches_pipeline:
        decision.note = "replay diverged from supercluster_by_modularity_peak; treat the trace as indicative only"
    return decision, meta_graph
