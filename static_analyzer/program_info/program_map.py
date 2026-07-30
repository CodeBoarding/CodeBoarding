"""Exact profiles for the bounded ProgramMap groups shown to agents."""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

import networkx as nx

from static_analyzer.graph import ClusterResult
from static_analyzer.program_info.errors import InvalidClusterCoverError, InvalidWeightError
from static_analyzer.program_info.models import Channel, ProgramInformation
from static_analyzer.program_info.topology import analyze_topology


@dataclass(frozen=True)
class InterGroupFlow:
    group_id: int
    weight: float
    channels: tuple[tuple[Channel, float], ...]


@dataclass(frozen=True)
class ProgramGroupProfile:
    group_id: int
    cluster_ids: tuple[int, ...]
    symbols: tuple[str, ...]
    symbol_count: int
    file_count: int
    package_count: int
    raw_channel_mix: tuple[tuple[Channel, int], ...]
    weighted_channel_mix: tuple[tuple[Channel, float], ...]
    internal_flow: float
    incoming_flow: float
    outgoing_flow: float
    cohesion: float
    coupling: float
    flow_entropy: float
    flow_concentration: float
    scc_count: int
    cyclic_scc_count: int
    maximum_dependency_depth: int
    entries: tuple[str, ...]
    exits: tuple[str, ...]
    hubs: tuple[str, ...]
    bridges: tuple[str, ...]
    boundary_symbols: tuple[str, ...]
    incoming_groups: tuple[InterGroupFlow, ...]
    outgoing_groups: tuple[InterGroupFlow, ...]


def build_group_profiles(
    cluster_result: ClusterResult,
    information: ProgramInformation,
    groups: list[set[int]],
    limit: int = 8,
) -> tuple[ProgramGroupProfile, ...]:
    """Join the exact fitted groups to leaf ownership and authoritative evidence."""
    if limit < 1:
        raise ValueError("Program-map profile limit must be positive")
    cluster_ids = set(cluster_result.clusters)
    seen_clusters: set[int] = set()
    for index, group in enumerate(groups):
        if not isinstance(group, set) or not group:
            raise InvalidClusterCoverError(f"Malformed fitted group {index}: expected a nonempty set")
        unknown = group - cluster_ids
        duplicate = group & seen_clusters
        if unknown:
            raise InvalidClusterCoverError(f"Fitted group {index} contains unknown clusters {sorted(unknown)}")
        if duplicate:
            raise InvalidClusterCoverError(f"Leaf clusters have duplicate group ownership: {sorted(duplicate)}")
        seen_clusters.update(group)
    missing_clusters = cluster_ids - seen_clusters
    if missing_clusters:
        raise InvalidClusterCoverError(f"Fitted groups omit leaf clusters {sorted(missing_clusters)}")
    information.cluster_profiles(cluster_result.clusters)

    owner: dict[str, int] = {}
    members_by_group: list[set[str]] = []
    for group_id, group in enumerate(groups):
        members = {name for cluster_id in group for name in cluster_result.clusters[cluster_id]}
        duplicate = members & owner.keys()
        if duplicate:
            raise InvalidClusterCoverError(f"Symbols have duplicate fitted-group ownership: {sorted(duplicate)}")
        owner.update({name: group_id for name in members})
        members_by_group.append(members)

    crossing: dict[tuple[int, int], Counter[Channel]] = defaultdict(Counter)
    for edge in information.edges:
        value = edge.weighted_value
        if not math.isfinite(value):
            raise InvalidWeightError(f"Non-finite profile flow {edge.source} -> {edge.destination}")
        source_group, destination_group = owner[edge.source], owner[edge.destination]
        if source_group != destination_group:
            crossing[(source_group, destination_group)][edge.channel] += value

    profiles = []
    symbol_profiles = {profile.qualified_name: profile for profile in information.symbol_profiles()}
    for group_id, (group, members) in enumerate(zip(groups, members_by_group)):
        raw: Counter[Channel] = Counter()
        weighted: Counter[Channel] = Counter()
        internal = incoming = outgoing = 0.0
        boundary: set[str] = set()
        touched_weights: list[float] = []
        internal_graph = nx.DiGraph()
        internal_graph.add_nodes_from(sorted(members))
        external_in: Counter[str] = Counter()
        external_out: Counter[str] = Counter()
        for edge in information.edges:
            source_inside, destination_inside = edge.source in members, edge.destination in members
            if not source_inside and not destination_inside:
                continue
            value = edge.weighted_value
            raw[edge.channel] += edge.count
            weighted[edge.channel] += value
            touched_weights.append(value)
            if source_inside and destination_inside:
                internal += value
                internal_graph.add_edge(edge.source, edge.destination)
            elif source_inside:
                outgoing += value
                external_out[edge.source] += value
                boundary.add(edge.source)
            else:
                incoming += value
                external_in[edge.destination] += value
                boundary.add(edge.destination)
        total = internal + incoming + outgoing
        probabilities = [weight / sum(touched_weights) for weight in touched_weights if sum(touched_weights)]
        topology = analyze_topology(internal_graph)
        rank = lambda values: tuple(sorted(values, key=lambda name: (-values[name], name))[:limit])
        hub_scores = {
            name: symbol_profiles[name].weighted_fan_in + symbol_profiles[name].weighted_fan_out for name in members
        }
        facts = [information.symbol(name) for name in members]
        outgoing_groups = _rank_group_flows(crossing, group_id, True, limit)
        incoming_groups = _rank_group_flows(crossing, group_id, False, limit)
        profiles.append(
            ProgramGroupProfile(
                group_id,
                tuple(sorted(group)),
                tuple(sorted(members)),
                len(members),
                len({fact.file_path for fact in facts}),
                len({fact.package for fact in facts}),
                tuple(sorted(raw.items())),
                tuple(sorted(weighted.items())),
                internal,
                incoming,
                outgoing,
                internal / total if total else 0.0,
                (incoming + outgoing) / total if total else 0.0,
                -sum(p * math.log2(p) for p in probabilities),
                sum(p * p for p in probabilities),
                len(topology.regions),
                sum(region.cyclic for region in topology.regions),
                topology.maximum_depth,
                rank(external_in),
                rank(external_out),
                rank(hub_scores),
                topology.bridges[:limit],
                tuple(sorted(boundary))[:limit],
                incoming_groups,
                outgoing_groups,
            )
        )
    return tuple(profiles)


def _rank_group_flows(
    crossing: dict[tuple[int, int], Counter[Channel]], group_id: int, outgoing: bool, limit: int
) -> tuple[InterGroupFlow, ...]:
    flows = []
    for (source, destination), channels in crossing.items():
        if (source if outgoing else destination) != group_id:
            continue
        peer = destination if outgoing else source
        flows.append(InterGroupFlow(peer, sum(channels.values()), tuple(sorted(channels.items()))))
    return tuple(sorted(flows, key=lambda flow: (-flow.weight, flow.group_id))[:limit])
