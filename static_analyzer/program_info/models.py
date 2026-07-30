"""Immutable program-information domain models and analysis."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath
from types import MappingProxyType

import networkx as nx

from static_analyzer.program_info.errors import InvalidClusterCoverError, InvalidWeightError, UnknownEndpointError


class Channel(StrEnum):
    CALL = "call"
    CONTAINS = "contains"
    INHERITS = "inherits"
    TYPEREF = "typeref"
    IMPORT = "import"


CHANNEL_WEIGHTS: dict[Channel, float] = {
    Channel.CALL: 1.0,
    Channel.CONTAINS: 1.0,
    Channel.INHERITS: 1.25,
    Channel.TYPEREF: 0.5,
    Channel.IMPORT: 0.25,
}


@dataclass(frozen=True, order=True)
class SymbolFact:
    qualified_name: str
    kind: int
    file_path: str
    line_start: int
    line_end: int
    col_start: int = 0
    detail: str = ""
    selection_span: tuple[int, int, int, int] = (0, 0, 0, 0)
    parent_chain: tuple[tuple[str, int], ...] = ()
    tags: tuple[int, ...] = ()
    deprecated: bool = False
    visibility: str = "unknown"
    modifiers: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()
    import_evidence: tuple[str, ...] = ()
    type_use_evidence: tuple[str, ...] = ()

    @property
    def package(self) -> str:
        return str(PurePath(self.file_path).parent)

    def fingerprint(self) -> str:
        payload = json.dumps(self, cls=_DomainEncoder, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, order=True)
class EdgeEvidence:
    source: str
    destination: str
    channel: Channel
    count: int = 1
    raw_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.count < 0 or not math.isfinite(self.raw_weight) or self.raw_weight < 0:
            raise InvalidWeightError(f"Invalid {self.channel} evidence {self.source} -> {self.destination}")

    @property
    def weighted_value(self) -> float:
        multiplicity = max(1, self.count) if self.channel == Channel.CALL else self.raw_weight
        return multiplicity * CHANNEL_WEIGHTS[self.channel]

    @property
    def key(self) -> tuple[str, str, Channel]:
        return self.source, self.destination, self.channel


@dataclass(frozen=True)
class GraphStatistics:
    symbol_count: int
    edge_count: int
    evidence_count: int
    total_weight: float
    channel_counts: tuple[tuple[Channel, int], ...]
    isolated_symbols: int
    density: float


@dataclass(frozen=True)
class SymbolProfile:
    qualified_name: str
    weighted_fan_in: float
    weighted_fan_out: float
    caller_count: int
    callee_count: int
    structural_neighbor_count: int
    incoming_channels: tuple[tuple[Channel, float], ...]
    outgoing_channels: tuple[tuple[Channel, float], ...]


@dataclass(frozen=True)
class SourceFactProfile:
    public_count: int
    private_count: int
    deprecated_count: int
    annotation_count: int
    import_evidence_count: int
    type_use_evidence_count: int
    unresolved_source_fact_count: int


@dataclass(frozen=True)
class FlowLens:
    entries: tuple[str, ...]
    exits: tuple[str, ...]
    hubs: tuple[str, ...]
    boundary_symbols: tuple[str, ...]
    internal_weight: float
    incoming_weight: float
    outgoing_weight: float


@dataclass(frozen=True)
class ClusterProfile:
    cluster_id: int
    symbol_count: int
    file_count: int
    package_count: int
    internal_weight: float
    incoming_weight: float
    outgoing_weight: float
    cohesion: float
    coupling: float
    channel_composition: tuple[tuple[Channel, float], ...]
    lens: FlowLens


@dataclass(frozen=True)
class ProgramDelta:
    added_symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...]
    changed_symbols: tuple[str, ...]
    added_edges: tuple[tuple[str, str, Channel], ...]
    removed_edges: tuple[tuple[str, str, Channel], ...]
    changed_edges: tuple[tuple[str, str, Channel], ...]
    statistics_changed: bool

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.added_symbols,
                self.removed_symbols,
                self.changed_symbols,
                self.added_edges,
                self.removed_edges,
                self.changed_edges,
                self.statistics_changed,
            )
        )


@dataclass(frozen=True)
class ProgramSnapshot:
    symbols: tuple[SymbolFact, ...]
    edges: tuple[EdgeEvidence, ...]
    statistics: GraphStatistics
    fingerprint: str

    @classmethod
    def create(cls, information: ProgramInformation) -> ProgramSnapshot:
        payload = {
            "symbols": information.symbols,
            "edges": information.edges,
            "statistics": information.statistics,
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, cls=_DomainEncoder, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        return cls(information.symbols, information.edges, information.statistics, fingerprint)

    def compare(self, newer: ProgramSnapshot) -> ProgramDelta:
        old_symbols = {fact.qualified_name: fact for fact in self.symbols}
        new_symbols = {fact.qualified_name: fact for fact in newer.symbols}
        old_edges = {edge.key: edge for edge in self.edges}
        new_edges = {edge.key: edge for edge in newer.edges}
        return ProgramDelta(
            added_symbols=tuple(sorted(new_symbols.keys() - old_symbols.keys())),
            removed_symbols=tuple(sorted(old_symbols.keys() - new_symbols.keys())),
            changed_symbols=tuple(
                sorted(name for name in old_symbols.keys() & new_symbols if old_symbols[name] != new_symbols[name])
            ),
            added_edges=tuple(sorted(new_edges.keys() - old_edges.keys())),
            removed_edges=tuple(sorted(old_edges.keys() - new_edges.keys())),
            changed_edges=tuple(
                sorted(key for key in old_edges.keys() & new_edges if old_edges[key] != new_edges[key])
            ),
            statistics_changed=self.statistics != newer.statistics,
        )


@dataclass(frozen=True)
class ProgramInformation:
    symbols: tuple[SymbolFact, ...]
    edges: tuple[EdgeEvidence, ...]
    _symbol_index: dict[str, SymbolFact] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        index = {symbol.qualified_name: symbol for symbol in self.symbols}
        if len(index) != len(self.symbols):
            raise ValueError("Program information contains duplicate symbols")
        for edge in self.edges:
            if edge.source not in index or edge.destination not in index:
                raise UnknownEndpointError(f"Unknown endpoint in {edge.source} -> {edge.destination}")
        if tuple(sorted(self.symbols)) != self.symbols or tuple(sorted(self.edges)) != self.edges:
            raise ValueError("Program information must use deterministic ordering")
        object.__setattr__(self, "_symbol_index", MappingProxyType(index))

    def symbol(self, qualified_name: str) -> SymbolFact:
        """Return one authoritative symbol fact."""
        try:
            return self._symbol_index[qualified_name]
        except KeyError as exc:
            raise KeyError(f"Unknown program symbol: {qualified_name}") from exc

    def incoming_evidence(self, qualified_name: str, channels: set[Channel] | None = None) -> tuple[EdgeEvidence, ...]:
        self.symbol(qualified_name)
        return tuple(
            edge
            for edge in self.edges
            if edge.destination == qualified_name and (channels is None or edge.channel in channels)
        )

    def outgoing_evidence(self, qualified_name: str, channels: set[Channel] | None = None) -> tuple[EdgeEvidence, ...]:
        self.symbol(qualified_name)
        return tuple(
            edge
            for edge in self.edges
            if edge.source == qualified_name and (channels is None or edge.channel in channels)
        )

    def neighbors(self, qualified_name: str, channels: set[Channel] | None = None) -> tuple[str, ...]:
        """Return sorted bidirectional neighbors selected by evidence channel."""
        edges = self.incoming_evidence(qualified_name, channels) + self.outgoing_evidence(qualified_name, channels)
        return tuple(
            sorted(
                {
                    edge.source if edge.destination == qualified_name else edge.destination
                    for edge in edges
                    if edge.source != edge.destination
                }
            )
        )

    def subgraph_by_symbols(self, qualified_names: set[str]) -> ProgramInformation:
        missing = qualified_names - self._symbol_index.keys()
        if missing:
            raise KeyError(f"Unknown program symbols: {sorted(missing)}")
        return ProgramInformation(
            tuple(symbol for symbol in self.symbols if symbol.qualified_name in qualified_names),
            tuple(
                edge for edge in self.edges if edge.source in qualified_names and edge.destination in qualified_names
            ),
        )

    def subgraph_by_files(self, file_paths: set[str]) -> ProgramInformation:
        return self.subgraph_by_symbols(
            {symbol.qualified_name for symbol in self.symbols if symbol.file_path in file_paths}
        )

    def subgraph_by_packages(self, packages: set[str]) -> ProgramInformation:
        return self.subgraph_by_symbols(
            {symbol.qualified_name for symbol in self.symbols if symbol.package in packages}
        )

    @property
    def statistics(self) -> GraphStatistics:
        channels: Counter[Channel] = Counter()
        total = 0.0
        touched: set[str] = set()
        for edge in self.edges:
            channels[edge.channel] += edge.count
            total += edge.weighted_value
            touched.update((edge.source, edge.destination))
        n = len(self.symbols)
        return GraphStatistics(
            symbol_count=n,
            edge_count=len(self.edges),
            evidence_count=sum(edge.count for edge in self.edges),
            total_weight=total,
            channel_counts=tuple(sorted(channels.items())),
            isolated_symbols=n - len(touched),
            density=len(self.edges) / (n * (n - 1)) if n > 1 else 0.0,
        )

    def projection(self, structural_channels: set[str] | None = None) -> nx.DiGraph:
        if structural_channels is None:
            from static_analyzer.program_info.projection import to_projection

            return to_projection(self)
        enabled = set(structural_channels or {channel.value for channel in Channel if channel != Channel.CALL})
        graph = nx.DiGraph()
        for symbol in self.symbols:
            graph.add_node(
                symbol.qualified_name,
                file_path=symbol.file_path,
                line_start=symbol.line_start,
                line_end=symbol.line_end,
                type=symbol.kind,
                deprecated=symbol.deprecated,
            )
        for edge in self.edges:
            if edge.channel != Channel.CALL and edge.channel.value not in enabled:
                continue
            attrs = graph.get_edge_data(edge.source, edge.destination, {}).copy()
            attrs[edge.channel.value] = attrs.get(edge.channel.value, 0.0) + edge.weighted_value
            attrs[f"{edge.channel.value}_count"] = attrs.get(f"{edge.channel.value}_count", 0) + edge.count
            attrs["weight"] = attrs.get("weight", 0.0) + edge.weighted_value
            graph.add_edge(edge.source, edge.destination, **attrs)
        return graph

    def symbol_profiles(self) -> tuple[SymbolProfile, ...]:
        incoming: dict[str, Counter[Channel]] = defaultdict(Counter)
        outgoing: dict[str, Counter[Channel]] = defaultdict(Counter)
        callers: dict[str, set[str]] = defaultdict(set)
        callees: dict[str, set[str]] = defaultdict(set)
        structural: dict[str, set[str]] = defaultdict(set)
        for edge in self.edges:
            outgoing[edge.source][edge.channel] += edge.weighted_value
            incoming[edge.destination][edge.channel] += edge.weighted_value
            if edge.channel == Channel.CALL:
                callees[edge.source].add(edge.destination)
                callers[edge.destination].add(edge.source)
            else:
                structural[edge.source].add(edge.destination)
                structural[edge.destination].add(edge.source)
        return tuple(
            SymbolProfile(
                symbol.qualified_name,
                sum(incoming[symbol.qualified_name].values()),
                sum(outgoing[symbol.qualified_name].values()),
                len(callers[symbol.qualified_name]),
                len(callees[symbol.qualified_name]),
                len(structural[symbol.qualified_name]),
                tuple(sorted(incoming[symbol.qualified_name].items())),
                tuple(sorted(outgoing[symbol.qualified_name].items())),
            )
            for symbol in self.symbols
        )

    def source_fact_profile(self, qualified_names: set[str] | None = None) -> SourceFactProfile:
        """Return bounded aggregate source-fact counts for a program or module."""
        selected = (
            self.symbols
            if qualified_names is None
            else tuple(symbol for symbol in self.symbols if symbol.qualified_name in qualified_names)
        )
        imports = sum(len(symbol.import_evidence) for symbol in selected)
        type_uses = sum(len(symbol.type_use_evidence) for symbol in selected)
        structural = sum(
            edge.count
            for edge in self.edges
            if edge.channel in {Channel.IMPORT, Channel.TYPEREF}
            and edge.source in {symbol.qualified_name for symbol in selected}
        )
        return SourceFactProfile(
            sum(symbol.visibility == "public" for symbol in selected),
            sum(symbol.visibility == "private" for symbol in selected),
            sum(symbol.deprecated for symbol in selected),
            sum(len(symbol.annotations) for symbol in selected),
            imports,
            type_uses,
            max(0, imports + type_uses - structural),
        )

    def cluster_profiles(self, clusters: dict[int, set[str]], limit: int = 8) -> tuple[ClusterProfile, ...]:
        owner: dict[str, int] = {}
        for cluster_id, members in sorted(clusters.items()):
            unknown = members - self._symbol_index.keys()
            duplicate = members & owner.keys()
            if unknown or duplicate:
                raise InvalidClusterCoverError(
                    f"Cluster {cluster_id} has unknown={sorted(unknown)} duplicate={sorted(duplicate)}"
                )
            owner.update({member: cluster_id for member in members})
        missing = self._symbol_index.keys() - owner.keys()
        if missing:
            raise InvalidClusterCoverError(f"Cluster cover omits {sorted(missing)}")

        profiles = {profile.qualified_name: profile for profile in self.symbol_profiles()}
        result: list[ClusterProfile] = []
        for cluster_id, members in sorted(clusters.items()):
            internal = incoming = outgoing = 0.0
            channels: Counter[Channel] = Counter()
            boundary: set[str] = set()
            for edge in self.edges:
                source_inside = edge.source in members
                destination_inside = edge.destination in members
                if source_inside and destination_inside:
                    internal += edge.weighted_value
                    channels[edge.channel] += edge.weighted_value
                elif source_inside:
                    outgoing += edge.weighted_value
                    boundary.add(edge.source)
                elif destination_inside:
                    incoming += edge.weighted_value
                    boundary.add(edge.destination)
            ranked_in = sorted(members, key=lambda name: (-profiles[name].weighted_fan_in, name))
            ranked_out = sorted(members, key=lambda name: (-profiles[name].weighted_fan_out, name))
            entries = tuple(name for name in ranked_in if profiles[name].weighted_fan_in > 0)[:limit]
            exits = tuple(name for name in ranked_out if profiles[name].weighted_fan_out > 0)[:limit]
            hubs = tuple(
                sorted(
                    members,
                    key=lambda name: (-(profiles[name].weighted_fan_in + profiles[name].weighted_fan_out), name),
                )[:limit]
            )
            facts = [self._symbol_index[name] for name in members]
            possible = len(members) * max(1, len(members) - 1)
            crossing = incoming + outgoing
            result.append(
                ClusterProfile(
                    cluster_id,
                    len(members),
                    len({fact.file_path for fact in facts}),
                    len({fact.package for fact in facts}),
                    internal,
                    incoming,
                    outgoing,
                    internal / possible,
                    crossing / (internal + crossing) if internal + crossing else 0.0,
                    tuple(sorted(channels.items())),
                    FlowLens(entries, exits, hubs, tuple(sorted(boundary))[:limit], internal, incoming, outgoing),
                )
            )
        return tuple(result)

    def snapshot(self) -> ProgramSnapshot:
        return ProgramSnapshot.create(self)


class _DomainEncoder(json.JSONEncoder):
    def default(self, obj: object) -> object:
        if hasattr(obj, "__dataclass_fields__"):
            return {name: getattr(obj, name) for name in obj.__dataclass_fields__ if not name.startswith("_")}
        if isinstance(obj, StrEnum):
            return obj.value
        return super().default(obj)
