"""Build a flow-based program map from static-analysis leaf clusters.

Two stages, both deterministic:

1. **Map** — hierarchical Infomap over a weighted, directed meta-graph of
   inter-cluster calls identifies modules that trap program flow. The closest
   hierarchy level is deterministically fitted to the architecture's size range.
   The LLM only names the result.
2. **Absorption** — real call graphs carry a long tail of leaf clusters with no
   inter-cluster edge at all, which Infomap leaves as singletons. Each is folded
   into the nearest seed by call proximity, then by directory affinity, with the
   smaller seed winning ties so the tail spreads instead of piling onto one
   component.
"""

import logging
import os
from collections import Counter, defaultdict, deque
from dataclasses import dataclass

import infomap
import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import ClusteringConfig, Language
from static_analyzer.graph import ClusterResult
from static_analyzer.program_info.program_map import ProgramGroupProfile, build_group_profiles
from static_analyzer.program_info.projection import from_projection
from static_analyzer.program_info.models import ProgramInformation, SymbolFact

logger = logging.getLogger(__name__)

# Range for the number of top-level architecture components. The exact count N
# inside this range is selected from Infomap's program-flow hierarchy, not by
# the LLM, so the component structure is stable across re-runs.
TOP_LEVEL_COMPONENTS_MIN = 5
TOP_LEVEL_COMPONENTS_MAX = 8

# Same idea for a component's sub-components (one level down); a component is
# usually smaller than the whole repo, so the floor is lower.
SUBCOMPONENTS_MIN = 3
SUBCOMPONENTS_MAX = 8

INFOMAP_TRIALS = 10


class ProgramMapExecutionError(RuntimeError):
    """Infomap could not execute for the requested program-map scope."""


def _profile_information(cluster_result: ClusterResult, graph: nx.DiGraph) -> ProgramInformation:
    """Decode evidence, with concrete facts for an entirely absent legacy projection."""
    information = from_projection(graph)
    if information.symbols or not cluster_result.clusters:
        return information
    symbols: list[SymbolFact] = []
    for cluster_id, members in sorted(cluster_result.clusters.items()):
        file_path = min(cluster_result.cluster_to_files.get(cluster_id, {""}))
        symbols.extend(SymbolFact(name, 0, file_path, 0, 0) for name in sorted(members))
    return ProgramInformation(tuple(sorted(symbols)), ())


def build_all_cluster_results(static_analysis: StaticAnalysisResults) -> dict[str, ClusterResult]:
    """Cluster every detected language and give the clusters a shared ID namespace.

    Downstream code maps ``cluster_id -> component`` in a single dict, so IDs must
    not collide across languages.
    """
    cluster_results: dict[str, ClusterResult] = {}
    offset = 0
    for lang in static_analysis.get_languages():
        result = static_analysis.get_cfg(lang).cluster()
        if offset:
            result = reindex_cluster_result(result, offset)
            logger.info(f"[Cluster] {lang}: offset IDs by +{offset} ({len(result.clusters)} clusters)")
        cluster_results[str(lang)] = result
        offset += max(result.clusters, default=0) + 1

    _sync_cluster_cache(static_analysis, cluster_results)
    return cluster_results


def _sync_cluster_cache(static_analysis: StaticAnalysisResults, cluster_results: dict[str, ClusterResult]) -> None:
    """Keep each CFG cache aligned with returned cluster IDs."""
    for lang, result in cluster_results.items():
        try:
            cfg = static_analysis.get_cfg(Language(lang))
            cfg._cluster_cache = result
            cfg.record_cluster_paths(result)
        except ValueError:
            logger.warning("Could not sync cluster cache for missing language %s", lang)


def reindex_across_languages(cluster_results: dict[str, ClusterResult]) -> None:
    """Give each language's clusters a disjoint ID range, in place.

    Needed wherever per-language ``ClusterResult``s are built independently (the
    per-component subgraphs) and then merged into one ``cluster_id -> component``
    lookup.
    """
    if len(cluster_results) <= 1:
        return
    # Already disjoint across languages — e.g. the seeded incremental path returned the
    # previous run's scoped ids. Re-offsetting would drift a stable namespace every run,
    # so a TypeScript sub-cluster saved as 2/3 would become 4/5 next time and the persisted
    # lineage would never settle. Only the cold path (each language freshly clustered from 1)
    # overlaps and needs reindexing.
    ranges = sorted((min(r.clusters), max(r.clusters)) for r in cluster_results.values() if r.clusters)
    if all(ranges[i][0] > ranges[i - 1][1] for i in range(1, len(ranges))):
        return
    offset = 0
    for lang in sorted(cluster_results):
        result = cluster_results[lang]
        if offset:
            cluster_results[lang] = reindex_cluster_result(result, offset)
            logger.info(f"[ReIndex] {lang}: offset IDs by +{offset} (now {offset + 1}-{offset + len(result.clusters)})")
        offset += max(result.clusters, default=0) + 1


def reindex_cluster_result(cluster_result: ClusterResult, offset: int) -> ClusterResult:
    """Return a copy of *cluster_result* with every cluster ID shifted by *offset*."""
    new_clusters: dict[int, set[str]] = {}
    new_cluster_to_files: dict[int, set[str]] = {}
    new_file_to_clusters: dict[str, set[int]] = defaultdict(set)

    for old_id, nodes in cluster_result.clusters.items():
        new_id = old_id + offset
        new_clusters[new_id] = nodes
        if old_id in cluster_result.cluster_to_files:
            new_cluster_to_files[new_id] = cluster_result.cluster_to_files[old_id]

    for file_path, old_ids in cluster_result.file_to_clusters.items():
        new_file_to_clusters[file_path] = {old_id + offset for old_id in old_ids}

    return ClusterResult(
        clusters=new_clusters,
        cluster_to_files=new_cluster_to_files,
        file_to_clusters=dict(new_file_to_clusters),
        strategy=cluster_result.strategy,
    )


# ---------------------------------------------------------------------------
# Meta-graph construction
# ---------------------------------------------------------------------------


def _build_meta_graph(cluster_result: ClusterResult, cfg_graph: nx.DiGraph) -> nx.DiGraph:
    """Build a weighted directed meta-graph of inter-cluster connectivity.

    Each node is a cluster ID. Each edge ``(src_cid, dst_cid)`` carries the
    number of CFG calls from ``src_cid`` members into ``dst_cid`` members.
    Mutual coupling A<->B becomes two separate flows, preserving direction for
    Infomap and for the incremental drift score.
    """
    node_to_cluster: dict[str, int] = {}
    for cluster_id, nodes in cluster_result.clusters.items():
        for node in nodes:
            node_to_cluster[node] = cluster_id

    meta_graph = nx.DiGraph()
    for cid in sorted(cluster_result.clusters):
        meta_graph.add_node(cid)

    edge_weights: dict[tuple[int, int], float] = defaultdict(float)
    for src, dst, data in cfg_graph.edges(data=True):
        src_cid = node_to_cluster.get(src)
        dst_cid = node_to_cluster.get(dst)
        if src_cid is not None and dst_cid is not None and src_cid != dst_cid:
            edge_weights[(src_cid, dst_cid)] += float(data.get("weight", 1.0))

    for (src_cid, dst_cid), weight in sorted(edge_weights.items()):
        meta_graph.add_edge(src_cid, dst_cid, weight=weight)

    return meta_graph


def group_symbols(cluster_ids: list[int], node_lookup: dict[int, set[str]]) -> list[str]:
    """Qualified names in a group, most top-level first (fewest name segments)."""
    names = {qname for cid in cluster_ids for qname in node_lookup.get(cid, set())}
    return sorted(names, key=lambda qname: (qname.count("."), qname))


def combine_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterResult:
    """Union per-language ClusterResults into one.

    Cluster IDs are globally unique across languages, so a plain union is safe and
    lets us group every language's leaf clusters against a single meta-graph.
    """
    clusters: dict[int, set[str]] = {}
    cluster_to_files: dict[int, set[str]] = {}
    file_to_clusters: dict[str, set[int]] = defaultdict(set)
    for cr in cluster_results.values():
        clusters.update(cr.clusters)
        cluster_to_files.update(cr.cluster_to_files)
        for file_path, cids in cr.file_to_clusters.items():
            file_to_clusters[file_path].update(cids)
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=dict(file_to_clusters),
        strategy="combined",
    )


# ---------------------------------------------------------------------------
# Program map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgramMap:
    """Infomap's hierarchy and the bounded module view consumed by agents."""

    groups: list[set[int]]
    node_flow: dict[int, float]
    module_paths: dict[int, tuple[int, ...]]
    codelength: float
    compression: float
    hierarchy_levels: int
    profiles: tuple[ProgramGroupProfile, ...] = ()

    def group_flow(self, group: set[int]) -> float:
        return sum(self.node_flow.get(cluster_id, 0.0) for cluster_id in group)

    def group_profile(self, group: set[int]) -> ProgramGroupProfile:
        """Return the profile whose exact fitted cluster identity matches *group*."""
        identity = tuple(sorted(group))
        for profile in self.profiles:
            if profile.cluster_ids == identity:
                return profile
        raise KeyError(f"Unknown fitted ProgramMap group: {list(identity)}")


def _partition_at_depth(module_paths: dict[int, tuple[int, ...]], depth: int) -> list[set[int]]:
    modules: dict[tuple[int, ...], set[int]] = defaultdict(set)
    for cluster_id, path in module_paths.items():
        modules[path[: min(depth, len(path))]].add(cluster_id)
    return list(modules.values())


def _select_hierarchy_partition(module_paths: dict[int, tuple[int, ...]], low: int, high: int) -> list[set[int]]:
    """Select the coarsest Infomap hierarchy level inside the component budget."""
    max_depth = max((len(path) for path in module_paths.values()), default=1)
    partitions = [_partition_at_depth(module_paths, depth) for depth in range(1, max_depth + 1)]
    in_range = [partition for partition in partitions if low <= len(partition) <= high]
    if in_range:
        return in_range[0]

    def range_distance(partition: list[set[int]]) -> tuple[int, int]:
        count = len(partition)
        distance = low - count if count < low else count - high
        return distance, -count

    return min(partitions, key=range_distance)


def _cluster_packages(cid: int, cluster_result: ClusterResult) -> set[str]:
    """Directories of the files a leaf cluster touches (its 'package')."""
    return {os.path.dirname(path) for path in cluster_result.cluster_to_files.get(cid, set())}


def _package_affinity(package: str, candidates: set[str]) -> int:
    """Leading path segments *package* shares with its closest match in *candidates*."""
    own = package.split(os.sep)
    best = 0
    for candidate in candidates:
        other = candidate.split(os.sep)
        shared = 0
        for a, b in zip(own, other):
            if a != b:
                break
            shared += 1
        best = max(best, shared)
    return best


def _seed_distances(meta_graph: nx.DiGraph, seeds: list[set[int]]) -> list[dict[int, int]]:
    """Hop distance from each seed to every leaf cluster it can reach.

    One multi-source BFS per seed on an undirected view — absorption is about
    topological proximity, not directional reachability, and a tiny utility
    cluster should be absorbable regardless of which way the calls flow.
    """
    undirected = meta_graph.to_undirected(as_view=True) if meta_graph.is_directed() else meta_graph
    distances: list[dict[int, int]] = []
    for seed in seeds:
        reached = {cid: 0 for cid in seed if undirected.has_node(cid)}
        frontier = deque(reached)
        while frontier:
            cid = frontier.popleft()
            for neighbour in undirected.neighbors(cid):
                if neighbour not in reached:
                    reached[neighbour] = reached[cid] + 1
                    frontier.append(neighbour)
        distances.append(reached)
    return distances


def _absorb_leftovers(
    seeds: list[set[int]],
    leftovers: list[int],
    meta_graph: nx.DiGraph,
    cluster_result: ClusterResult,
    method_count: dict[int, int],
) -> None:
    """Fold every leftover leaf cluster into a seed, in place.

    Preference order per leftover: fewest hops to a seed member, then deepest
    shared directory prefix with the seed's *original* packages, then the
    smallest seed. Ties always go to the smaller seed, and affinity is measured
    against the seed's pre-absorption packages — otherwise a seed that absorbed
    early keeps widening its package set and wins every later comparison, which
    is what collapses a repo into one mega-component.
    """
    distances = _seed_distances(meta_graph, seeds)
    seed_packages = [{pkg for cid in seed for pkg in _cluster_packages(cid, cluster_result)} for seed in seeds]
    sizes = [sum(method_count.get(cid, 0) for cid in seed) for seed in seeds]

    for cid in leftovers:
        ranked = [(reached[cid], sizes[idx], idx) for idx, reached in enumerate(distances) if cid in reached]
        if not ranked:
            packages = _cluster_packages(cid, cluster_result)
            for idx, seed_pkgs in enumerate(seed_packages):
                affinity = max((_package_affinity(pkg, seed_pkgs) for pkg in packages), default=0)
                if affinity:
                    ranked.append((-affinity, sizes[idx], idx))
        if not ranked:
            ranked = [(0, sizes[idx], idx) for idx in range(len(seeds))]
        target = min(ranked)[2]
        seeds[target].add(cid)
        sizes[target] += method_count.get(cid, 0)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _method_counts(cluster_result: ClusterResult) -> dict[int, int]:
    return {cid: len(members) for cid, members in cluster_result.clusters.items()}


def _fit_partition_to_budget(
    communities: list[set[int]],
    meta_graph: nx.DiGraph,
    cluster_result: ClusterResult,
    method_count: dict[int, int],
    low: int,
    high: int,
) -> list[set[int]]:
    """Bound an Infomap hierarchy cut while retaining every leaf cluster."""
    modules = sorted(
        (set(community) for community in communities if len(community) >= 2),
        key=lambda group: (-sum(method_count.get(cid, 0) for cid in group), min(group)),
    )
    singletons = sorted(
        (next(iter(community)) for community in communities if len(community) == 1),
        key=lambda cid: (-method_count.get(cid, 0), cid),
    )
    seeds = modules[:high]
    overflow = modules[high:]

    while len(seeds) < low and singletons:
        seeds.append({singletons.pop(0)})
    if not seeds and singletons:
        seeds.append({singletons.pop(0)})

    for community in overflow:
        sizes = [sum(method_count.get(cid, 0) for cid in seed) for seed in seeds]
        coupling = [
            sum(
                float(meta_graph.get_edge_data(src, dst, {}).get("weight", 0.0))
                + float(meta_graph.get_edge_data(dst, src, {}).get("weight", 0.0))
                for src in community
                for dst in seed
            )
            for seed in seeds
        ]
        target = min(range(len(seeds)), key=lambda index: (-coupling[index], sizes[index], index))
        seeds[target].update(community)

    if seeds:
        _absorb_leftovers(seeds, singletons, meta_graph, cluster_result, method_count)

    while len(seeds) < low:
        splittable = [group for group in seeds if len(group) > 1]
        if not splittable:
            break
        source = max(splittable, key=lambda group: (sum(method_count.get(cid, 0) for cid in group), -min(group)))
        promoted = min(source, key=lambda cid: (-method_count.get(cid, 0), cid))
        source.remove(promoted)
        seeds.append({promoted})

    return sorted(seeds, key=lambda group: min(group))


def _score_program_partition(meta_graph: nx.DiGraph, groups: list[set[int]], seed: int) -> tuple[float, float]:
    """Evaluate the exact bounded partition without letting Infomap move nodes."""
    if meta_graph.number_of_edges() == 0:
        return 0.0, 0.0
    initial_partition = {
        cluster_id: module_id for module_id, group in enumerate(groups, start=1) for cluster_id in group
    }
    try:
        result = infomap.run(
            meta_graph,
            directed=True,
            seed=seed,
            initial_partition=initial_partition,
            options=infomap.Options(two_level=True, no_infomap=True),
        )
    except Exception as exc:
        raise ProgramMapExecutionError(
            f"Infomap fixed-partition scoring failed for {meta_graph.number_of_nodes()} clusters "
            f"and {meta_graph.number_of_edges()} edges"
        ) from exc
    return result.codelength, max(0.0, result.relative_codelength_savings)


def build_program_map(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    low: int = TOP_LEVEL_COMPONENTS_MIN,
    high: int = TOP_LEVEL_COMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> ProgramMap:
    """Map directed program flow between static-analysis leaf clusters with Infomap."""
    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    n_leaf = meta_graph.number_of_nodes()
    if n_leaf == 0:
        information = _profile_information(cluster_result, cfg_graph)
        return ProgramMap([], {}, {}, 0.0, 0.0, 0, build_group_profiles(cluster_result, information, []))
    high = min(high, n_leaf)
    if meta_graph.number_of_edges() == 0:
        isolated_paths: dict[int, tuple[int, ...]] = {
            cid: (index,) for index, cid in enumerate(sorted(meta_graph.nodes), start=1)
        }
        communities = [{cid} for cid in sorted(meta_graph.nodes)]
        counts = _method_counts(cluster_result)
        total = sum(counts.values()) or n_leaf
        node_flow = {cid: counts.get(cid, 1) / total for cid in meta_graph.nodes}
        groups = _fit_partition_to_budget(communities, meta_graph, cluster_result, counts, low, high)
        information = _profile_information(cluster_result, cfg_graph)
        return ProgramMap(
            groups, node_flow, isolated_paths, 0.0, 0.0, 1, build_group_profiles(cluster_result, information, groups)
        )

    try:
        result = infomap.run(meta_graph, directed=True, seed=seed, num_trials=INFOMAP_TRIALS)
    except Exception as exc:
        raise ProgramMapExecutionError(
            f"Infomap optimization failed for {n_leaf} clusters and {meta_graph.number_of_edges()} edges"
        ) from exc
    module_paths = {int(cluster_id): path for cluster_id, path in result.multilevel_modules().items()}
    node_flow = {int(node.node_id): node.flow for node in result.nodes()}
    if n_leaf <= low:
        groups = [{cid} for cid in sorted(meta_graph.nodes)]
    else:
        communities = _select_hierarchy_partition(module_paths, low, high)
        groups = _fit_partition_to_budget(
            communities, meta_graph, cluster_result, _method_counts(cluster_result), low, high
        )
    codelength, compression = _score_program_partition(meta_graph, groups, seed)
    program_map = ProgramMap(
        groups=groups,
        node_flow=node_flow,
        module_paths=module_paths,
        codelength=codelength,
        compression=compression,
        hierarchy_levels=max((len(path) for path in module_paths.values()), default=0),
        profiles=build_group_profiles(cluster_result, _profile_information(cluster_result, cfg_graph), groups),
    )
    logger.info(
        f"[ProgramMap] {meta_graph.number_of_nodes()} leaf clusters -> {len(groups)} flow modules "
        f"(codelength={program_map.codelength:.4f}, compression={program_map.compression:.1%}, "
        f"hierarchy={program_map.hierarchy_levels}, sizes {sorted((len(g) for g in groups), reverse=True)})"
    )
    return program_map


def build_program_map_for_languages(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    low: int = TOP_LEVEL_COMPONENTS_MIN,
    high: int = TOP_LEVEL_COMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> ProgramMap:
    """Build one program map across all languages in the current static-analysis scope.

    Leaf-cluster IDs are globally unique, so every language shares one component
    budget and one Infomap hierarchy before the result enters the agentic layer.
    """
    combined = combine_cluster_results(cluster_results)
    combined_cfg: nx.DiGraph = nx.compose_all(list(cfg_graphs.values())) if cfg_graphs else nx.DiGraph()
    return build_program_map(combined, combined_cfg, low, high, seed)


# ---------------------------------------------------------------------------
# Anchored regrouping (the incremental path)
# ---------------------------------------------------------------------------

# How far the carried grouping's compression may trail the fresh ProgramMap
# before the structure is re-derived.
REGROUP_DRIFT_BUDGET = 0.10


def _inherit_ids(
    groups: list[set[int]],
    previous_owner: dict[int, str],
    method_count: dict[int, int],
) -> list[str]:
    """Give each group the id of the previous component whose code it mostly holds.

    Used even when the structure is re-derived from scratch: a regrouping that renamed
    every component would light up the whole diagram, when in truth most of the code
    stayed where it was. Weighted by method count, biggest claim first, one id per
    group, so the dominant successor of a component keeps its identity and only
    genuinely new groups come out unnamed.
    """
    claims: list[tuple[int, Counter[str]]] = []
    for index, group in enumerate(groups):
        tally: Counter[str] = Counter()
        for cid in group:
            owner = previous_owner.get(cid)
            if owner:
                tally[owner] += method_count.get(cid, 0)
        claims.append((index, tally))

    owners = [""] * len(groups)
    taken: set[str] = set()
    for index, tally in sorted(claims, key=lambda claim: (-max(claim[1].values(), default=0), claim[0])):
        for owner, _weight in tally.most_common():
            if owner not in taken:
                taken.add(owner)
                owners[index] = owner
                break
    return owners


@dataclass(frozen=True)
class AnchoredGrouping:
    """A grouping carried forward from the previous run, plus what it cost."""

    groups: list[set[int]]
    #: index into ``groups`` -> the component id it inherited, or "" when new.
    owners: list[str]
    #: True when drift forced a from-scratch re-derivation rather than a carry-forward.
    regrouped: bool


def anchored_grouping(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    previous_owner: dict[int, str],
    low: int = TOP_LEVEL_COMPONENTS_MIN,
    high: int = TOP_LEVEL_COMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
    drift_budget: float = REGROUP_DRIFT_BUDGET,
) -> AnchoredGrouping:
    """Repair the previous grouping against a new clustering instead of re-deriving one.

    ``build_program_map`` re-optimizes from scratch. Flow-map optimization has a
    degenerate solution landscape — many partitions score within noise of each other —
    so a two-line diff can select a different near-optimal partition and reshuffle which
    component owns what. Deterministic, but not continuous, and the incremental path
    needs continuity: a component's identity has to survive a change that did not touch it.

    So each live leaf cluster simply keeps the component that owned it
    (``previous_owner``, from the baseline's ``source_cluster_ids``); genuinely new
    clusters are absorbed into the nearest existing group; and a component left holding
    nothing is dropped. No re-partitioning at all in the steady state.

    The escape hatch is ``drift_budget``: when the carried-forward grouping scores that
    much worse than a fresh optimum, the code really has moved on, and the result is a
    from-scratch regrouping with ``regrouped=True`` so the caller can say so out loud.
    """
    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    live = set(meta_graph.nodes)
    if not live:
        return AnchoredGrouping([], [], False)
    method_count = _method_counts(cluster_result)

    # Carry forward: one group per surviving component, in a stable id order.
    carried: dict[str, set[int]] = defaultdict(set)
    for cid in sorted(live):
        owner = previous_owner.get(cid)
        if owner:
            carried[owner].add(cid)
    if not carried:
        # Nothing to anchor to — a first run, or a baseline that shares no cluster.
        fresh = build_program_map(cluster_result, cfg_graph, low, high, seed).groups
        return AnchoredGrouping(fresh, [""] * len(fresh), True)

    owners = sorted(carried)
    groups = [carried[owner] for owner in owners]
    fresh_map = build_program_map(cluster_result, cfg_graph, low, high, seed)
    fresh_groups = fresh_map.groups
    newcomers = set(live) - {cid for group in groups for cid in group}

    # A whole new subsystem arriving must become its own component, not be scattered into
    # the existing ones by absorption. The from-scratch partition already isolates it: a
    # fresh community made *entirely* of new clusters is a subsystem the carried grouping
    # has no home for. Promote those as new (unowned) groups; absorb only the rest.
    new_subsystems = [group for group in fresh_groups if len(group) >= 2 and group <= newcomers]
    for subsystem in new_subsystems:
        groups.append(set(subsystem))
        owners.append("")
    absorbed = sorted(newcomers - {cid for subsystem in new_subsystems for cid in subsystem})
    if absorbed:
        _absorb_leftovers(groups, absorbed, meta_graph, cluster_result, method_count)

    effective_high = min(high, len(live))
    if len(groups) > effective_high:
        logger.info(
            f"[Anchored] carried grouping has {len(groups)} components above "
            f"the {effective_high} maximum; re-deriving structure from ProgramMap"
        )
        return AnchoredGrouping(fresh_groups, _inherit_ids(fresh_groups, previous_owner, method_count), True)

    _, carried_compression = _score_program_partition(meta_graph, groups, seed)
    if fresh_map.compression - carried_compression > drift_budget:
        logger.info(
            f"[Anchored] carried compression {carried_compression:.1%} vs "
            f"{fresh_map.compression:.1%} fresh (> {drift_budget:.1%} budget); "
            "re-deriving structure from ProgramMap"
        )
        return AnchoredGrouping(fresh_groups, _inherit_ids(fresh_groups, previous_owner, method_count), True)

    logger.info(
        f"[Anchored] {len(live)} leaf clusters -> {len(groups)} components carried forward "
        f"({len(new_subsystems)} new component(s), {len(absorbed)} clusters absorbed, "
        f"compression={carried_compression:.1%} vs {fresh_map.compression:.1%} fresh)"
    )
    return AnchoredGrouping(groups, owners, False)
