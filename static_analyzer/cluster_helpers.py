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

from __future__ import annotations

import logging
import math
import os
from collections import Counter, defaultdict, deque
from dataclasses import dataclass

import infomap
import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import ClusteringConfig, Language
from static_analyzer.graph import ClusterResult

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
PROGRAM_MAP_PROFILE_LIMIT = 8


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


def _validate_profile_groups(cluster_result: ClusterResult, groups: list[set[int]]) -> None:
    """Require the fitted groups to cover each leaf cluster exactly once."""
    expected = set(cluster_result.clusters)
    assigned: set[int] = set()
    for group_id, group in enumerate(groups):
        if not group:
            raise ValueError(f"Program-map group {group_id} is empty")
        unknown = group - expected
        duplicate = group & assigned
        if unknown:
            raise ValueError(f"Program-map group {group_id} contains unknown clusters {sorted(unknown)}")
        if duplicate:
            raise ValueError(f"Program-map groups share clusters {sorted(duplicate)}")
        assigned.update(group)
    if missing := expected - assigned:
        raise ValueError(f"Program-map groups omit clusters {sorted(missing)}")


def _rank_flow_symbols(weights: Counter[str], limit: int) -> tuple[str, ...]:
    return tuple(name for name, _ in sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _group_dependency_depth(graph: nx.DiGraph) -> tuple[int, int, int, tuple[str, ...]]:
    """Measure SCC regions and their condensed dependency depth."""
    if not graph:
        return 0, 0, 0, ()
    regions = sorted(
        (tuple(sorted(region)) for region in nx.strongly_connected_components(graph)), key=lambda region: region
    )
    owner = {symbol: index for index, region in enumerate(regions) for symbol in region}
    condensed = nx.DiGraph()
    condensed.add_nodes_from(range(len(regions)))
    condensed.add_edges_from(
        sorted({(owner[source], owner[target]) for source, target in graph.edges if owner[source] != owner[target]})
    )
    depth: dict[int, int] = {}
    for region_id in nx.lexicographical_topological_sort(condensed, key=lambda index: regions[index]):
        depth[region_id] = max((depth[parent] + 1 for parent in condensed.predecessors(region_id)), default=0)
    cyclic = sum(len(region) > 1 or graph.has_edge(region[0], region[0]) for region in regions)
    bridges = tuple(sorted(nx.articulation_points(graph.to_undirected()))) if len(graph) > 1 else ()
    return len(regions), cyclic, max(depth.values(), default=0), bridges


def _rank_group_flows(
    flows: dict[tuple[int, int], float],
    group_id: int,
    outgoing: bool,
    limit: int,
) -> tuple[InterGroupFlow, ...]:
    ranked = [
        InterGroupFlow(destination if outgoing else source, weight)
        for (source, destination), weight in flows.items()
        if (source if outgoing else destination) == group_id
    ]
    return tuple(sorted(ranked, key=lambda flow: (-flow.weight, flow.group_id))[:limit])


def build_program_map_profiles(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    groups: list[set[int]],
    limit: int = PROGRAM_MAP_PROFILE_LIMIT,
) -> tuple[ProgramGroupProfile, ...]:
    """Describe the exact bounded groups using their directed weighted program flow."""
    if limit < 1:
        raise ValueError("Program-map profile limit must be positive")
    _validate_profile_groups(cluster_result, groups)

    cluster_by_symbol = {
        symbol: cluster_id for cluster_id, symbols in cluster_result.clusters.items() for symbol in symbols
    }
    group_by_cluster = {cluster_id: group_id for group_id, group in enumerate(groups) for cluster_id in group}
    symbols_by_group = [
        {symbol for cluster_id in group for symbol in cluster_result.clusters[cluster_id]} for group in groups
    ]
    internal_graphs = [nx.DiGraph() for _ in groups]
    for graph, symbols in zip(internal_graphs, symbols_by_group):
        graph.add_nodes_from(sorted(symbols))

    internal = [0.0] * len(groups)
    incoming = [0.0] * len(groups)
    outgoing = [0.0] * len(groups)
    touched_weights: list[list[float]] = [[] for _ in groups]
    entries = [Counter[str]() for _ in groups]
    exits = [Counter[str]() for _ in groups]
    hubs = [Counter[str]() for _ in groups]
    boundaries = [set[str]() for _ in groups]
    group_flows: dict[tuple[int, int], float] = defaultdict(float)

    for source, destination, data in cfg_graph.edges(data=True):
        source_cluster = cluster_by_symbol.get(source)
        destination_cluster = cluster_by_symbol.get(destination)
        if source_cluster is None or destination_cluster is None:
            continue
        weight = float(data.get("weight", 1.0))
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"Program-map edge {source} -> {destination} has invalid weight {weight}")
        source_group = group_by_cluster[source_cluster]
        destination_group = group_by_cluster[destination_cluster]
        if source_group == destination_group:
            internal[source_group] += weight
            touched_weights[source_group].append(weight)
            internal_graphs[source_group].add_edge(source, destination, weight=weight)
            hubs[source_group][source] += weight
            hubs[source_group][destination] += weight
            continue
        outgoing[source_group] += weight
        incoming[destination_group] += weight
        touched_weights[source_group].append(weight)
        touched_weights[destination_group].append(weight)
        exits[source_group][source] += weight
        entries[destination_group][destination] += weight
        hubs[source_group][source] += weight
        hubs[destination_group][destination] += weight
        boundaries[source_group].add(source)
        boundaries[destination_group].add(destination)
        group_flows[source_group, destination_group] += weight

    profiles = []
    for group_id, (group, symbols) in enumerate(zip(groups, symbols_by_group)):
        files = tuple(
            sorted({path for cluster_id in group for path in cluster_result.cluster_to_files.get(cluster_id, set())})
        )
        packages = tuple(sorted({os.path.dirname(path) for path in files}))
        total = internal[group_id] + incoming[group_id] + outgoing[group_id]
        weights = touched_weights[group_id]
        denominator = sum(weights)
        probabilities = [weight / denominator for weight in weights] if denominator else []
        regions, cyclic, depth, bridges = _group_dependency_depth(internal_graphs[group_id])
        possible_internal_edges = len(symbols) * max(1, len(symbols) - 1)
        profiles.append(
            ProgramGroupProfile(
                cluster_ids=tuple(sorted(group)),
                symbols=tuple(sorted(symbols)),
                files=files,
                packages=packages,
                internal_flow=internal[group_id],
                incoming_flow=incoming[group_id],
                outgoing_flow=outgoing[group_id],
                cohesion=internal[group_id] / total if total else 0.0,
                coupling=(incoming[group_id] + outgoing[group_id]) / total if total else 0.0,
                flow_entropy=-sum(probability * math.log2(probability) for probability in probabilities),
                flow_concentration=sum(probability * probability for probability in probabilities),
                strongly_connected_regions=regions,
                cyclic_regions=cyclic,
                maximum_dependency_depth=depth,
                entries=_rank_flow_symbols(entries[group_id], limit),
                exits=_rank_flow_symbols(exits[group_id], limit),
                hubs=_rank_flow_symbols(hubs[group_id], limit),
                bridges=bridges[:limit],
                boundary_symbols=tuple(sorted(boundaries[group_id]))[:limit],
                incoming_groups=_rank_group_flows(group_flows, group_id, False, limit),
                outgoing_groups=_rank_group_flows(group_flows, group_id, True, limit),
            )
        )
    return tuple(profiles)


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
class InterGroupFlow:
    """Weighted directed flow between two fitted program-map groups."""

    group_id: int
    weight: float


@dataclass(frozen=True)
class ProgramGroupProfile:
    """Flow, topology, and boundary facts for one fitted program-map group."""

    cluster_ids: tuple[int, ...]
    symbols: tuple[str, ...]
    files: tuple[str, ...]
    packages: tuple[str, ...]
    internal_flow: float
    incoming_flow: float
    outgoing_flow: float
    cohesion: float
    coupling: float
    flow_entropy: float
    flow_concentration: float
    strongly_connected_regions: int
    cyclic_regions: int
    maximum_dependency_depth: int
    entries: tuple[str, ...]
    exits: tuple[str, ...]
    hubs: tuple[str, ...]
    bridges: tuple[str, ...]
    boundary_symbols: tuple[str, ...]
    incoming_groups: tuple[InterGroupFlow, ...]
    outgoing_groups: tuple[InterGroupFlow, ...]


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
        """Return the profile for the exact fitted group identity."""
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
    result = infomap.run(
        meta_graph,
        directed=True,
        seed=seed,
        initial_partition=initial_partition,
        options=infomap.Options(two_level=True, no_infomap=True),
    )
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
        return ProgramMap([], {}, {}, 0.0, 0.0, 0, ())
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
        profiles = build_program_map_profiles(cluster_result, cfg_graph, groups)
        return ProgramMap(groups, node_flow, isolated_paths, 0.0, 0.0, 1, profiles)

    result = infomap.run(meta_graph, directed=True, seed=seed, num_trials=INFOMAP_TRIALS)
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
        profiles=build_program_map_profiles(cluster_result, cfg_graph, groups),
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
