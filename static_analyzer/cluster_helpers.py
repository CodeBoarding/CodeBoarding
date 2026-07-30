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

import hashlib
import json
import logging
import math
import os
from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from pathlib import PurePath
from types import MappingProxyType

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

PROGRAM_MAP_CHANNEL_WEIGHTS: dict[str, float] = {
    "call": 1.0,
    "contains": 1.0,
    "inherits": 1.25,
    "typeref": 0.5,
    "import": 0.25,
}


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


class ProgramMapInformationError(ValueError):
    """Program-map evidence cannot be represented faithfully."""


class ProgramMapUnknownEndpointError(ProgramMapInformationError):
    """Evidence points outside the program-map symbol set."""


class ProgramMapInvalidWeightError(ProgramMapInformationError):
    """Evidence has an invalid weight or multiplicity."""


class ProgramMapSnapshotError(ProgramMapInformationError):
    """Persisted program-map information is malformed or stale."""


@dataclass(frozen=True, order=True)
class ProgramMapSymbol:
    """Stable source location metadata carried into the program map."""

    qualified_name: str
    kind: int
    file_path: str
    line_start: int
    line_end: int
    col_start: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.qualified_name:
            raise ProgramMapInformationError("Program-map symbol must have a qualified name")
        if self.line_start < 0 or self.line_end < self.line_start or self.col_start < 0:
            raise ProgramMapInformationError(f"Program-map symbol has invalid location: {self.qualified_name}")

    @property
    def package(self) -> str:
        return str(PurePath(self.file_path).parent)


@dataclass(frozen=True, order=True)
class ProgramMapEvidence:
    """One typed contribution to a directed program-map edge."""

    source: str
    destination: str
    channel: str
    count: int = 1
    raw_weight: float = 1.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.channel, str)
            or self.channel not in PROGRAM_MAP_CHANNEL_WEIGHTS
            or isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 0
            or isinstance(self.raw_weight, bool)
            or not isinstance(self.raw_weight, (int, float))
            or not math.isfinite(self.raw_weight)
            or self.raw_weight < 0
        ):
            raise ProgramMapInvalidWeightError(f"Invalid {self.channel} evidence {self.source} -> {self.destination}")

    @property
    def weighted_value(self) -> float:
        multiplicity = max(1, self.count) if self.channel == "call" else self.raw_weight
        return multiplicity * PROGRAM_MAP_CHANNEL_WEIGHTS[self.channel]

    @property
    def key(self) -> tuple[str, str, str]:
        return self.source, self.destination, self.channel


@dataclass(frozen=True)
class ProgramMapStatistics:
    """Global evidence totals used to compare equivalent program-map runs."""

    symbol_count: int
    edge_count: int
    evidence_count: int
    total_weight: float
    channel_counts: tuple[tuple[str, int], ...]
    isolated_symbols: int
    density: float


@dataclass(frozen=True)
class ProgramMapSymbolProfile:
    """Directed fan-in, fan-out, and structural-neighbour facts for one symbol."""

    qualified_name: str
    weighted_fan_in: float
    weighted_fan_out: float
    caller_count: int
    callee_count: int
    structural_neighbor_count: int
    incoming_channels: tuple[tuple[str, float], ...]
    outgoing_channels: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ProgramMapChannelProfile:
    """Coverage and directionality facts for one typed program-map channel."""

    channel: str
    evidence_count: int
    occurrence_count: int
    weighted_total: float
    source_count: int
    destination_count: int
    self_reference_count: int
    reciprocal_pair_count: int
    top_sources: tuple[tuple[str, float], ...]
    top_destinations: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ProgramMapChannelAnalysis:
    """Complete typed-evidence coverage facts for a program-map projection."""

    profiles: tuple[ProgramMapChannelProfile, ...]
    typed_symbol_coverage: float
    unreferenced_symbols: tuple[str, ...]

    def profile(self, channel: str) -> ProgramMapChannelProfile:
        """Return one observed channel profile by stable edge kind."""
        for profile in self.profiles:
            if profile.channel == channel:
                return profile
        raise KeyError(f"Program-map channel is not represented: {channel}")


@dataclass(frozen=True)
class ProgramMapFlowFacts:
    """Bounded weighted-flow facts for a selected symbol scope."""

    total_weight: float
    internal_weight: float
    crossing_weight: float
    internal_ratio: float
    entropy: float
    concentration: float
    channel_mix: tuple[tuple[str, float], ...]
    top_incoming: tuple[tuple[str, float], ...]
    top_outgoing: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ProgramMapStrongRegion:
    """One strongly connected region and its layer in the condensation DAG."""

    members: tuple[str, ...]
    cyclic: bool
    layer: int


@dataclass(frozen=True)
class ProgramMapTopology:
    """Deterministic topology facts for an evidence projection."""

    regions: tuple[ProgramMapStrongRegion, ...]
    sources: tuple[str, ...]
    sinks: tuple[str, ...]
    bridges: tuple[str, ...]
    maximum_depth: int


@dataclass(frozen=True)
class ProgramMapDelta:
    """Symbol and evidence changes between two stable program-map snapshots."""

    added_symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...]
    changed_symbols: tuple[str, ...]
    added_evidence: tuple[tuple[str, str, str], ...]
    removed_evidence: tuple[tuple[str, str, str], ...]
    changed_evidence: tuple[tuple[str, str, str], ...]
    statistics_changed: bool

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.added_symbols,
                self.removed_symbols,
                self.changed_symbols,
                self.added_evidence,
                self.removed_evidence,
                self.changed_evidence,
                self.statistics_changed,
            )
        )


@dataclass(frozen=True)
class ProgramMapSnapshot:
    """Content-addressable program-map state for an incremental comparison."""

    symbols: tuple[ProgramMapSymbol, ...]
    evidence: tuple[ProgramMapEvidence, ...]
    statistics: ProgramMapStatistics
    fingerprint: str

    @classmethod
    def create(cls, information: ProgramMapInformation) -> ProgramMapSnapshot:
        payload = cls._content_payload(information)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return cls(information.symbols, information.evidence, information.statistics, fingerprint)

    @staticmethod
    def _content_payload(information: ProgramMapInformation) -> dict[str, object]:
        return {
            "symbols": [asdict(symbol) for symbol in information.symbols],
            "evidence": [{**asdict(item), "channel": item.channel} for item in information.evidence],
            "statistics": {
                **asdict(information.statistics),
                "channel_counts": [[channel, count] for channel, count in information.statistics.channel_counts],
            },
        }

    def to_payload(self) -> dict[str, object]:
        """Return a versioned, deterministic payload suitable for the incremental cache."""
        payload = self._content_payload(ProgramMapInformation(self.symbols, self.evidence))
        return {"format": 1, **payload, "fingerprint": self.fingerprint}

    def to_json(self) -> str:
        """Encode the snapshot without whitespace that could vary between runs."""
        return json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, encoded: str) -> ProgramMapSnapshot:
        """Decode one persisted program-map snapshot after validating its complete content."""
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ProgramMapSnapshotError("Program-map snapshot is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ProgramMapSnapshotError("Program-map snapshot must be a JSON object")
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ProgramMapSnapshot:
        """Decode a versioned payload and reject cache data whose fingerprint no longer matches."""
        if payload.get("format") != 1:
            raise ProgramMapSnapshotError("Unsupported program-map snapshot format")
        symbols = tuple(sorted(cls._decode_symbols(payload.get("symbols"))))
        evidence = tuple(sorted(cls._decode_evidence(payload.get("evidence"))))
        information = ProgramMapInformation(symbols, evidence)
        snapshot = cls.create(information)
        canonical = snapshot.to_payload()
        if dict(payload) != canonical:
            raise ProgramMapSnapshotError("Program-map snapshot content does not match its fingerprint")
        return snapshot

    @staticmethod
    def _decode_symbols(raw_symbols: object) -> tuple[ProgramMapSymbol, ...]:
        if not isinstance(raw_symbols, list):
            raise ProgramMapSnapshotError("Program-map snapshot symbols must be a list")
        symbols = []
        for index, raw_symbol in enumerate(raw_symbols):
            if not isinstance(raw_symbol, dict):
                raise ProgramMapSnapshotError(f"Program-map snapshot symbol {index} must be an object")
            symbols.append(
                ProgramMapSymbol(
                    _snapshot_string(raw_symbol.get("qualified_name"), f"symbols[{index}].qualified_name"),
                    _snapshot_integer(raw_symbol.get("kind"), f"symbols[{index}].kind"),
                    _snapshot_string(raw_symbol.get("file_path"), f"symbols[{index}].file_path"),
                    _snapshot_integer(raw_symbol.get("line_start"), f"symbols[{index}].line_start"),
                    _snapshot_integer(raw_symbol.get("line_end"), f"symbols[{index}].line_end"),
                    _snapshot_integer(raw_symbol.get("col_start"), f"symbols[{index}].col_start"),
                    _snapshot_string(raw_symbol.get("detail"), f"symbols[{index}].detail"),
                )
            )
        return tuple(symbols)

    @staticmethod
    def _decode_evidence(raw_evidence: object) -> tuple[ProgramMapEvidence, ...]:
        if not isinstance(raw_evidence, list):
            raise ProgramMapSnapshotError("Program-map snapshot evidence must be a list")
        evidence = []
        for index, raw_item in enumerate(raw_evidence):
            if not isinstance(raw_item, dict):
                raise ProgramMapSnapshotError(f"Program-map snapshot evidence {index} must be an object")
            channel = _snapshot_string(raw_item.get("channel"), f"evidence[{index}].channel")
            if channel not in PROGRAM_MAP_CHANNEL_WEIGHTS:
                raise ProgramMapSnapshotError(f"Program-map snapshot evidence {index} has an unknown channel")
            evidence.append(
                ProgramMapEvidence(
                    _snapshot_string(raw_item.get("source"), f"evidence[{index}].source"),
                    _snapshot_string(raw_item.get("destination"), f"evidence[{index}].destination"),
                    channel,
                    _snapshot_integer(raw_item.get("count"), f"evidence[{index}].count"),
                    _snapshot_number(raw_item.get("raw_weight"), f"evidence[{index}].raw_weight"),
                )
            )
        return tuple(evidence)

    def compare(self, newer: ProgramMapSnapshot) -> ProgramMapDelta:
        old_symbols = {symbol.qualified_name: symbol for symbol in self.symbols}
        new_symbols = {symbol.qualified_name: symbol for symbol in newer.symbols}
        old_evidence = {item.key: item for item in self.evidence}
        new_evidence = {item.key: item for item in newer.evidence}
        return ProgramMapDelta(
            added_symbols=tuple(sorted(new_symbols.keys() - old_symbols.keys())),
            removed_symbols=tuple(sorted(old_symbols.keys() - new_symbols.keys())),
            changed_symbols=tuple(
                sorted(
                    name for name in old_symbols.keys() & new_symbols.keys() if old_symbols[name] != new_symbols[name]
                )
            ),
            added_evidence=tuple(sorted(new_evidence.keys() - old_evidence.keys())),
            removed_evidence=tuple(sorted(old_evidence.keys() - new_evidence.keys())),
            changed_evidence=tuple(
                sorted(
                    key for key in old_evidence.keys() & new_evidence.keys() if old_evidence[key] != new_evidence[key]
                )
            ),
            statistics_changed=self.statistics != newer.statistics,
        )


def _snapshot_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProgramMapSnapshotError(f"Program-map snapshot {field_name} must be a string")
    return value


def _snapshot_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProgramMapSnapshotError(f"Program-map snapshot {field_name} must be an integer")
    return value


def _snapshot_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProgramMapSnapshotError(f"Program-map snapshot {field_name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class ProgramMapInformation:
    """Validated symbols and typed evidence used by flow-map clustering."""

    symbols: tuple[ProgramMapSymbol, ...]
    evidence: tuple[ProgramMapEvidence, ...]
    _symbol_index: dict[str, ProgramMapSymbol] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        index = {symbol.qualified_name: symbol for symbol in self.symbols}
        if len(index) != len(self.symbols):
            raise ProgramMapInformationError("Program-map information contains duplicate symbols")
        for item in self.evidence:
            if item.source not in index or item.destination not in index:
                raise ProgramMapUnknownEndpointError(f"Unknown endpoint in {item.source} -> {item.destination}")
        if len({item.key for item in self.evidence}) != len(self.evidence):
            raise ProgramMapInformationError("Program-map information contains duplicate typed evidence")
        if tuple(sorted(self.symbols)) != self.symbols or tuple(sorted(self.evidence)) != self.evidence:
            raise ProgramMapInformationError("Program-map information must use deterministic ordering")
        object.__setattr__(self, "_symbol_index", MappingProxyType(index))

    def symbol(self, qualified_name: str) -> ProgramMapSymbol:
        try:
            return self._symbol_index[qualified_name]
        except KeyError as exc:
            raise KeyError(f"Unknown program-map symbol: {qualified_name}") from exc

    @property
    def statistics(self) -> ProgramMapStatistics:
        channels: Counter[str] = Counter(item.channel for item in self.evidence)
        touched = {endpoint for item in self.evidence for endpoint in (item.source, item.destination)}
        count = len(self.symbols)
        return ProgramMapStatistics(
            symbol_count=count,
            edge_count=len({(item.source, item.destination) for item in self.evidence}),
            evidence_count=len(self.evidence),
            total_weight=sum(item.weighted_value for item in self.evidence),
            channel_counts=tuple(sorted(channels.items())),
            isolated_symbols=count - len(touched),
            density=len({(item.source, item.destination) for item in self.evidence}) / (count * max(1, count - 1)),
        )

    def incoming(self, qualified_name: str, channels: set[str] | None = None) -> tuple[ProgramMapEvidence, ...]:
        self.symbol(qualified_name)
        return tuple(
            item
            for item in self.evidence
            if item.destination == qualified_name and (channels is None or item.channel in channels)
        )

    def outgoing(self, qualified_name: str, channels: set[str] | None = None) -> tuple[ProgramMapEvidence, ...]:
        self.symbol(qualified_name)
        return tuple(
            item
            for item in self.evidence
            if item.source == qualified_name and (channels is None or item.channel in channels)
        )

    def symbol_profiles(self) -> tuple[ProgramMapSymbolProfile, ...]:
        incoming: dict[str, Counter[str]] = defaultdict(Counter)
        outgoing: dict[str, Counter[str]] = defaultdict(Counter)
        callers: dict[str, set[str]] = defaultdict(set)
        callees: dict[str, set[str]] = defaultdict(set)
        structural: dict[str, set[str]] = defaultdict(set)
        for item in self.evidence:
            incoming[item.destination][item.channel] += item.weighted_value
            outgoing[item.source][item.channel] += item.weighted_value
            if item.channel == "call":
                callers[item.destination].add(item.source)
                callees[item.source].add(item.destination)
            else:
                structural[item.source].add(item.destination)
                structural[item.destination].add(item.source)
        return tuple(
            ProgramMapSymbolProfile(
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

    def channel_analysis(self, limit: int = PROGRAM_MAP_PROFILE_LIMIT) -> ProgramMapChannelAnalysis:
        """Describe the coverage and directionality of every observed evidence channel."""
        return analyze_program_map_channels(self, limit)

    def snapshot(self) -> ProgramMapSnapshot:
        return ProgramMapSnapshot.create(self)


def _decode_program_map_evidence(
    source: str,
    destination: str,
    attrs: dict,
) -> tuple[ProgramMapEvidence, ...]:
    encoded = attrs.get("evidence")
    if encoded is None:
        weight = attrs.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0:
            raise ProgramMapInvalidWeightError(f"Program-map edge {source} -> {destination} has invalid weight")
        return (ProgramMapEvidence(source, destination, "call", max(1, int(weight)), 1.0),)
    if not isinstance(encoded, (tuple, list)):
        raise ProgramMapInformationError(f"Malformed evidence on {source} -> {destination}")
    result = []
    for item in encoded:
        try:
            channel, count, raw_weight = item
            channel = str(channel)
            if channel not in PROGRAM_MAP_CHANNEL_WEIGHTS:
                raise ValueError(channel)
            result.append(ProgramMapEvidence(source, destination, channel, int(count), float(raw_weight)))
        except (TypeError, ValueError) as exc:
            raise ProgramMapInformationError(f"Malformed evidence on {source} -> {destination}") from exc
    return tuple(sorted(result))


def build_program_map_information(graph: nx.DiGraph) -> ProgramMapInformation:
    """Decode the canonical program-map graph into deterministic typed evidence."""
    if not isinstance(graph, nx.DiGraph):
        raise ProgramMapInformationError("Program-map projection must be a directed graph")
    symbols = tuple(
        sorted(
            ProgramMapSymbol(
                str(name),
                int(attrs.get("type", attrs.get("kind", 0))),
                str(attrs.get("file_path", "")),
                int(attrs.get("line_start", 0)),
                int(attrs.get("line_end", 0)),
                int(attrs.get("col_start", 0)),
                str(attrs.get("detail", "")),
            )
            for name, attrs in graph.nodes(data=True)
        )
    )
    evidence = tuple(
        sorted(
            item
            for source, destination, attrs in graph.edges(data=True)
            for item in _decode_program_map_evidence(str(source), str(destination), attrs)
        )
    )
    return ProgramMapInformation(symbols, evidence)


def program_map_projection(information: ProgramMapInformation) -> nx.DiGraph:
    """Rebuild a deterministic NetworkX projection from typed program-map information."""
    graph = nx.DiGraph(program_map_evidence_codec=1)
    for symbol in information.symbols:
        graph.add_node(
            symbol.qualified_name,
            type=symbol.kind,
            file_path=symbol.file_path,
            line_start=symbol.line_start,
            line_end=symbol.line_end,
            col_start=symbol.col_start,
            detail=symbol.detail,
        )
    grouped: dict[tuple[str, str], list[ProgramMapEvidence]] = defaultdict(list)
    for item in information.evidence:
        grouped[item.source, item.destination].append(item)
    for (source, destination), items in sorted(grouped.items()):
        graph.add_edge(
            source,
            destination,
            evidence=tuple((item.channel, item.count, item.raw_weight) for item in items),
            weight=sum(item.weighted_value for item in items),
        )
    return graph


def analyze_program_map_channels(
    information: ProgramMapInformation,
    limit: int = PROGRAM_MAP_PROFILE_LIMIT,
) -> ProgramMapChannelAnalysis:
    """Measure how call and structural evidence cover the analyzed symbol space."""
    if limit < 1:
        raise ValueError("Program-map channel limit must be positive")
    evidence_by_channel: dict[str, list[ProgramMapEvidence]] = defaultdict(list)
    for item in information.evidence:
        evidence_by_channel[item.channel].append(item)
    profiles = []
    touched: set[str] = set()
    for channel, evidence in sorted(evidence_by_channel.items()):
        source_weights: Counter[str] = Counter()
        destination_weights: Counter[str] = Counter()
        directed_pairs = {(item.source, item.destination) for item in evidence}
        reciprocal_pairs = {
            tuple(sorted((source, destination)))
            for source, destination in directed_pairs
            if source != destination and (destination, source) in directed_pairs
        }
        for item in evidence:
            value = item.weighted_value
            source_weights[item.source] += value
            destination_weights[item.destination] += value
            touched.update((item.source, item.destination))
        rank = lambda weights: tuple(sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:limit])
        profiles.append(
            ProgramMapChannelProfile(
                channel=channel,
                evidence_count=len(evidence),
                occurrence_count=sum(item.count for item in evidence),
                weighted_total=sum(item.weighted_value for item in evidence),
                source_count=len(source_weights),
                destination_count=len(destination_weights),
                self_reference_count=sum(item.source == item.destination for item in evidence),
                reciprocal_pair_count=len(reciprocal_pairs),
                top_sources=rank(source_weights),
                top_destinations=rank(destination_weights),
            )
        )
    symbols = {symbol.qualified_name for symbol in information.symbols}
    return ProgramMapChannelAnalysis(
        profiles=tuple(profiles),
        typed_symbol_coverage=len(touched) / len(symbols) if symbols else 0.0,
        unreferenced_symbols=tuple(sorted(symbols - touched)),
    )


def analyze_program_map_flow(
    information: ProgramMapInformation,
    members: set[str],
    limit: int = PROGRAM_MAP_PROFILE_LIMIT,
) -> ProgramMapFlowFacts:
    """Summarize typed flow touching a selected program-map scope."""
    if limit < 1:
        raise ValueError("Program-map flow limit must be positive")
    unknown = members - information._symbol_index.keys()
    if unknown:
        raise KeyError(f"Unknown program-map symbols: {sorted(unknown)}")
    incoming: Counter[str] = Counter()
    outgoing: Counter[str] = Counter()
    channels: Counter[str] = Counter()
    internal = crossing = 0.0
    weights: list[float] = []
    for item in information.evidence:
        source_inside, destination_inside = item.source in members, item.destination in members
        if not source_inside and not destination_inside:
            continue
        value = item.weighted_value
        weights.append(value)
        channels[item.channel] += value
        if source_inside and destination_inside:
            internal += value
        else:
            crossing += value
        if source_inside:
            outgoing[item.source] += value
        if destination_inside:
            incoming[item.destination] += value
    total = internal + crossing
    denominator = sum(weights)
    probabilities = [weight / denominator for weight in weights] if denominator else []
    rank = lambda values: tuple(sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit])
    return ProgramMapFlowFacts(
        total,
        internal,
        crossing,
        internal / total if total else 0.0,
        -sum(probability * math.log2(probability) for probability in probabilities),
        sum(probability * probability for probability in probabilities),
        tuple(sorted(channels.items())),
        rank(incoming),
        rank(outgoing),
    )


def analyze_program_map_topology(information: ProgramMapInformation, members: set[str]) -> ProgramMapTopology:
    """Analyze SCCs and the condensation DAG for an exact program-map scope."""
    unknown = members - information._symbol_index.keys()
    if unknown:
        raise KeyError(f"Unknown program-map symbols: {sorted(unknown)}")
    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(members))
    graph.add_edges_from(
        sorted(
            (item.source, item.destination)
            for item in information.evidence
            if {item.source, item.destination} <= members
        )
    )
    if not graph:
        return ProgramMapTopology((), (), (), (), 0)
    regions = sorted(
        (tuple(sorted(region)) for region in nx.strongly_connected_components(graph)), key=lambda region: region
    )
    owner = {symbol: index for index, region in enumerate(regions) for symbol in region}
    dag = nx.DiGraph()
    dag.add_nodes_from(range(len(regions)))
    dag.add_edges_from(
        sorted(
            {
                (owner[source], owner[destination])
                for source, destination in graph.edges
                if owner[source] != owner[destination]
            }
        )
    )
    depths: dict[int, int] = {}
    for region_id in nx.lexicographical_topological_sort(dag, key=lambda index: regions[index]):
        depths[region_id] = max((depths[parent] + 1 for parent in dag.predecessors(region_id)), default=0)
    return ProgramMapTopology(
        regions=tuple(
            ProgramMapStrongRegion(
                region,
                len(region) > 1 or graph.has_edge(region[0], region[0]),
                depths[index],
            )
            for index, region in enumerate(regions)
        ),
        sources=tuple(symbol for symbol in sorted(graph) if graph.in_degree(symbol) == 0),
        sinks=tuple(symbol for symbol in sorted(graph) if graph.out_degree(symbol) == 0),
        bridges=tuple(sorted(nx.articulation_points(graph.to_undirected()))) if len(graph) > 1 else (),
        maximum_depth=max(depths.values(), default=0),
    )


@dataclass(frozen=True)
class ProgramMapModuleFlow:
    """Typed weighted flow between two caller-defined program-map modules."""

    source_module: int
    destination_module: int
    weight: float
    channels: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ProgramMapModuleProfile:
    """An exact module view independent of Infomap's fitted hierarchy level."""

    module_id: int
    members: tuple[str, ...]
    files: tuple[str, ...]
    packages: tuple[str, ...]
    flow: ProgramMapFlowFacts
    topology: ProgramMapTopology
    entry_symbols: tuple[str, ...]
    exit_symbols: tuple[str, ...]
    boundary_symbols: tuple[str, ...]
    cohesion: float
    coupling: float


@dataclass(frozen=True)
class ProgramMapModuleAnalysis:
    """Module profiles and their directed cross-boundary evidence."""

    profiles: tuple[ProgramMapModuleProfile, ...]
    inter_module_flow: tuple[ProgramMapModuleFlow, ...]


@dataclass(frozen=True)
class ProgramMapPackageFlow:
    """Typed weighted evidence that crosses from one source package to another."""

    source_package: str
    destination_package: str
    weight: float
    channels: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ProgramMapPackageProfile:
    """A source-tree package projected onto the typed program-map evidence."""

    package: str
    symbols: tuple[str, ...]
    files: tuple[str, ...]
    flow: ProgramMapFlowFacts
    topology: ProgramMapTopology
    entry_symbols: tuple[str, ...]
    exit_symbols: tuple[str, ...]
    boundary_symbols: tuple[str, ...]


@dataclass(frozen=True)
class ProgramMapPackageAnalysis:
    """Package ownership and cross-package evidence for a complete program map."""

    profiles: tuple[ProgramMapPackageProfile, ...]
    inter_package_flow: tuple[ProgramMapPackageFlow, ...]

    def profile(self, package: str) -> ProgramMapPackageProfile:
        """Return the exact source-tree package profile."""
        for profile in self.profiles:
            if profile.package == package:
                return profile
        raise KeyError(f"Program-map package is not represented: {package}")


def analyze_program_map_modules(
    information: ProgramMapInformation,
    partition: dict[int, set[str]],
    *,
    exact: bool = True,
    limit: int = PROGRAM_MAP_PROFILE_LIMIT,
) -> ProgramMapModuleAnalysis:
    """Evaluate an exact or scoped symbol partition using typed program-map evidence."""
    if limit < 1:
        raise ValueError("Program-map module limit must be positive")
    owner: dict[str, int] = {}
    known = set(information._symbol_index)
    for module_id, members in sorted(partition.items()):
        unknown = members - known
        duplicate = members & owner.keys()
        if unknown or duplicate:
            raise ProgramMapInformationError(
                f"Module {module_id} has unknown={sorted(unknown)} duplicate={sorted(duplicate)}"
            )
        owner.update((member, module_id) for member in members)
    if exact and known != owner.keys():
        raise ProgramMapInformationError(f"Module cover omits {sorted(known - owner.keys())}")

    crossing: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    boundaries: dict[int, set[str]] = {module_id: set() for module_id in partition}
    incoming: dict[int, Counter[str]] = {module_id: Counter() for module_id in partition}
    outgoing: dict[int, Counter[str]] = {module_id: Counter() for module_id in partition}
    for item in information.evidence:
        source_module = owner.get(item.source)
        destination_module = owner.get(item.destination)
        if source_module is None or destination_module is None or source_module == destination_module:
            continue
        crossing[source_module, destination_module][item.channel] += item.weighted_value
        boundaries[source_module].add(item.source)
        boundaries[destination_module].add(item.destination)
        outgoing[source_module][item.source] += item.weighted_value
        incoming[destination_module][item.destination] += item.weighted_value

    profiles = []
    for module_id, members in sorted(partition.items()):
        flow = analyze_program_map_flow(information, members, limit)
        topology = analyze_program_map_topology(information, members)
        facts = [information.symbol(member) for member in members]
        total = flow.internal_weight + flow.crossing_weight
        possible = len(members) * max(1, len(members) - 1)
        profiles.append(
            ProgramMapModuleProfile(
                module_id=module_id,
                members=tuple(sorted(members)),
                files=tuple(sorted({fact.file_path for fact in facts})),
                packages=tuple(sorted({fact.package for fact in facts})),
                flow=flow,
                topology=topology,
                entry_symbols=_rank_flow_symbols(incoming[module_id], limit),
                exit_symbols=_rank_flow_symbols(outgoing[module_id], limit),
                boundary_symbols=tuple(sorted(boundaries[module_id]))[:limit],
                cohesion=flow.internal_weight / possible,
                coupling=flow.crossing_weight / total if total else 0.0,
            )
        )
    flows = tuple(
        ProgramMapModuleFlow(source, destination, sum(channels.values()), tuple(sorted(channels.items())))
        for (source, destination), channels in sorted(crossing.items())
    )
    return ProgramMapModuleAnalysis(tuple(profiles), flows)


def analyze_program_map_packages(
    information: ProgramMapInformation,
    limit: int = PROGRAM_MAP_PROFILE_LIMIT,
) -> ProgramMapPackageAnalysis:
    """Project typed evidence onto source packages without changing Infomap membership."""
    if limit < 1:
        raise ValueError("Program-map package limit must be positive")
    package_by_symbol = {symbol.qualified_name: symbol.package for symbol in information.symbols}
    members: dict[str, set[str]] = defaultdict(set)
    files: dict[str, set[str]] = defaultdict(set)
    for symbol in information.symbols:
        members[symbol.package].add(symbol.qualified_name)
        files[symbol.package].add(symbol.file_path)
    incoming: dict[str, Counter[str]] = defaultdict(Counter)
    outgoing: dict[str, Counter[str]] = defaultdict(Counter)
    boundaries: dict[str, set[str]] = defaultdict(set)
    crossing: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for item in information.evidence:
        source_package = package_by_symbol[item.source]
        destination_package = package_by_symbol[item.destination]
        if source_package == destination_package:
            continue
        value = item.weighted_value
        crossing[source_package, destination_package][item.channel] += value
        outgoing[source_package][item.source] += value
        incoming[destination_package][item.destination] += value
        boundaries[source_package].add(item.source)
        boundaries[destination_package].add(item.destination)
    profiles = tuple(
        ProgramMapPackageProfile(
            package=package,
            symbols=tuple(sorted(symbols)),
            files=tuple(sorted(files[package])),
            flow=analyze_program_map_flow(information, symbols, limit),
            topology=analyze_program_map_topology(information, symbols),
            entry_symbols=_rank_flow_symbols(incoming[package], limit),
            exit_symbols=_rank_flow_symbols(outgoing[package], limit),
            boundary_symbols=tuple(sorted(boundaries[package]))[:limit],
        )
        for package, symbols in sorted(members.items())
    )
    flows = tuple(
        ProgramMapPackageFlow(source, destination, sum(channels.values()), tuple(sorted(channels.items())))
        for (source, destination), channels in sorted(crossing.items())
    )
    return ProgramMapPackageAnalysis(profiles, flows)


@dataclass(frozen=True)
class ProgramMapImpactedSymbol:
    """One non-seed symbol reached while tracing a program-map delta."""

    qualified_name: str
    depth: int
    directions: tuple[str, ...]
    channels: tuple[str, ...]


@dataclass(frozen=True)
class ProgramMapDeltaSurface:
    """Source-tree and evidence-channel surface directly touched by a map delta."""

    file_count: int
    package_count: int
    affected_files: tuple[str, ...]
    affected_packages: tuple[str, ...]
    channel_weight_delta: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ProgramMapDeltaSummary:
    """Bounded human-readable impact facts for an incremental program-map delta."""

    added_count: int = 0
    removed_count: int = 0
    changed_count: int = 0
    added_evidence_count: int = 0
    removed_evidence_count: int = 0
    changed_evidence_count: int = 0
    changed_channels: tuple[str, ...] = ()
    added_symbols: tuple[str, ...] = ()
    removed_symbols: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    impacted_symbols: tuple[ProgramMapImpactedSymbol, ...] = ()
    surface: ProgramMapDeltaSurface = field(default_factory=lambda: ProgramMapDeltaSurface(0, 0, (), (), ()))

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.added_count,
                self.removed_count,
                self.changed_count,
                self.added_evidence_count,
                self.removed_evidence_count,
                self.changed_evidence_count,
            )
        )

    def llm_str(self) -> str:
        """Render bounded impact facts for a component-group summary."""
        if self.is_empty:
            return ""
        parts = [
            f"program-map delta: symbols +{self.added_count}/-{self.removed_count}/~{self.changed_count}",
            f"evidence +{self.added_evidence_count}/-{self.removed_evidence_count}/~{self.changed_evidence_count}",
        ]
        if self.changed_channels:
            parts.append("channels " + ", ".join(channel for channel in self.changed_channels))
        if self.impacted_symbols:
            parts.append(
                "impact: "
                + ", ".join(
                    f"{item.qualified_name}@{item.depth}[{'/'.join(item.directions)}:"
                    f"{'/'.join(channel for channel in item.channels)}]"
                    for item in self.impacted_symbols
                )
            )
        if self.surface.affected_packages:
            parts.append("surface: " + ", ".join(self.surface.affected_packages))
        return "; ".join(parts)


def _program_map_impact(
    old: ProgramMapInformation,
    new: ProgramMapInformation,
    seeds: set[str],
    max_depth: int,
    limit: int,
) -> tuple[ProgramMapImpactedSymbol, ...]:
    adjacency: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for item in sorted(set(old.evidence) | set(new.evidence)):
        adjacency[item.source].append((item.destination, "out", item.channel))
        adjacency[item.destination].append((item.source, "in", item.channel))
    queue = deque((symbol, 0) for symbol in sorted(seeds))
    visited = set(seeds)
    facts: dict[tuple[str, int], tuple[set[str], set[str]]] = {}
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbour, direction, channel in sorted(
            adjacency.get(current, []), key=lambda item: (item[0], item[1], item[2])
        ):
            if neighbour in seeds:
                continue
            directions, channels = facts.setdefault((neighbour, depth + 1), (set(), set()))
            directions.add(direction)
            channels.add(channel)
            if neighbour not in visited:
                visited.add(neighbour)
                queue.append((neighbour, depth + 1))
    return tuple(
        ProgramMapImpactedSymbol(name, depth, tuple(sorted(directions)), tuple(sorted(channels)))
        for (name, depth), (directions, channels) in sorted(facts.items(), key=lambda item: (item[0][1], item[0][0]))[
            :limit
        ]
    )


def _program_map_delta_surface(
    delta: ProgramMapDelta,
    old: ProgramMapInformation,
    new: ProgramMapInformation,
    limit: int,
) -> ProgramMapDeltaSurface:
    old_symbols = {symbol.qualified_name: symbol for symbol in old.symbols}
    new_symbols = {symbol.qualified_name: symbol for symbol in new.symbols}
    affected_names = set(delta.added_symbols) | set(delta.removed_symbols) | set(delta.changed_symbols)
    changed_keys = delta.added_evidence + delta.removed_evidence + delta.changed_evidence
    affected_names.update(name for source, destination, _ in changed_keys for name in (source, destination))
    files: Counter[str] = Counter()
    packages: Counter[str] = Counter()
    for name in sorted(affected_names):
        symbol = new_symbols.get(name) or old_symbols.get(name)
        if symbol is None:
            continue
        files[symbol.file_path] += 1
        packages[symbol.package] += 1
    old_evidence = {item.key: item for item in old.evidence}
    new_evidence = {item.key: item for item in new.evidence}
    channel_delta: Counter[str] = Counter()
    for key in changed_keys:
        old_item = old_evidence.get(key)
        new_item = new_evidence.get(key)
        old_value = old_item.weighted_value if old_item is not None else 0.0
        new_value = new_item.weighted_value if new_item is not None else 0.0
        channel_delta[key[2]] += new_value - old_value
    rank = lambda values: tuple(
        name for name, _ in sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit]
    )
    return ProgramMapDeltaSurface(
        file_count=len(files),
        package_count=len(packages),
        affected_files=rank(files),
        affected_packages=rank(packages),
        channel_weight_delta=tuple(sorted((channel, weight) for channel, weight in channel_delta.items() if weight)),
    )


def summarize_program_map_delta(
    delta: ProgramMapDelta,
    old: ProgramMapInformation,
    new: ProgramMapInformation,
    *,
    max_depth: int = 2,
    limit: int = 12,
    symbol_limit: int = 5,
) -> ProgramMapDeltaSummary:
    """Summarize changed program-map evidence and its bounded bidirectional impact."""
    if max_depth < 0 or limit < 0 or symbol_limit < 0:
        raise ValueError("Program-map delta bounds must be non-negative")
    changed = delta.added_evidence + delta.removed_evidence + delta.changed_evidence
    channels = tuple(sorted({channel for _, _, channel in changed}))
    seeds = set(delta.added_symbols) | set(delta.removed_symbols) | set(delta.changed_symbols)
    seeds.update(endpoint for source, destination, _ in changed for endpoint in (source, destination))
    return ProgramMapDeltaSummary(
        added_count=len(delta.added_symbols),
        removed_count=len(delta.removed_symbols),
        changed_count=len(delta.changed_symbols),
        added_evidence_count=len(delta.added_evidence),
        removed_evidence_count=len(delta.removed_evidence),
        changed_evidence_count=len(delta.changed_evidence),
        changed_channels=channels,
        added_symbols=delta.added_symbols[:symbol_limit],
        removed_symbols=delta.removed_symbols[:symbol_limit],
        changed_symbols=delta.changed_symbols[:symbol_limit],
        impacted_symbols=_program_map_impact(old, new, seeds, max_depth, limit),
        surface=_program_map_delta_surface(delta, old, new, limit),
    )


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
    flows: dict[tuple[int, int], Counter[str]],
    group_id: int,
    outgoing: bool,
    limit: int,
) -> tuple[InterGroupFlow, ...]:
    ranked = [
        InterGroupFlow(destination if outgoing else source, sum(channels.values()), tuple(sorted(channels.items())))
        for (source, destination), channels in flows.items()
        if (source if outgoing else destination) == group_id
    ]
    return tuple(sorted(ranked, key=lambda flow: (-flow.weight, flow.group_id))[:limit])


def build_program_map_profiles(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    groups: list[set[int]],
    limit: int = PROGRAM_MAP_PROFILE_LIMIT,
    information: ProgramMapInformation | None = None,
) -> tuple[ProgramGroupProfile, ...]:
    """Describe the exact bounded groups using their directed weighted program flow."""
    if limit < 1:
        raise ValueError("Program-map profile limit must be positive")
    _validate_profile_groups(cluster_result, groups)
    information = information or build_program_map_information(cfg_graph)

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
    raw_channels = [Counter[str]() for _ in groups]
    weighted_channels = [Counter[str]() for _ in groups]
    group_flows: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)

    for item in information.evidence:
        source_cluster = cluster_by_symbol.get(item.source)
        destination_cluster = cluster_by_symbol.get(item.destination)
        if source_cluster is None or destination_cluster is None:
            continue
        weight = item.weighted_value
        source_group = group_by_cluster[source_cluster]
        destination_group = group_by_cluster[destination_cluster]
        if source_group == destination_group:
            internal[source_group] += weight
            touched_weights[source_group].append(weight)
            internal_graphs[source_group].add_edge(item.source, item.destination, weight=weight)
            hubs[source_group][item.source] += weight
            hubs[source_group][item.destination] += weight
            raw_channels[source_group][item.channel] += item.count
            weighted_channels[source_group][item.channel] += weight
            continue
        outgoing[source_group] += weight
        incoming[destination_group] += weight
        touched_weights[source_group].append(weight)
        touched_weights[destination_group].append(weight)
        exits[source_group][item.source] += weight
        entries[destination_group][item.destination] += weight
        hubs[source_group][item.source] += weight
        hubs[destination_group][item.destination] += weight
        boundaries[source_group].add(item.source)
        boundaries[destination_group].add(item.destination)
        raw_channels[source_group][item.channel] += item.count
        raw_channels[destination_group][item.channel] += item.count
        weighted_channels[source_group][item.channel] += weight
        weighted_channels[destination_group][item.channel] += weight
        group_flows[source_group, destination_group][item.channel] += weight

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
        topology = analyze_program_map_topology(information, symbols & information._symbol_index.keys())
        profiles.append(
            ProgramGroupProfile(
                group_id=group_id,
                cluster_ids=tuple(sorted(group)),
                symbols=tuple(sorted(symbols)),
                files=files,
                packages=packages,
                raw_channel_mix=tuple(sorted(raw_channels[group_id].items())),
                weighted_channel_mix=tuple(sorted(weighted_channels[group_id].items())),
                internal_flow=internal[group_id],
                incoming_flow=incoming[group_id],
                outgoing_flow=outgoing[group_id],
                cohesion=internal[group_id] / total if total else 0.0,
                coupling=(incoming[group_id] + outgoing[group_id]) / total if total else 0.0,
                flow_entropy=-sum(probability * math.log2(probability) for probability in probabilities),
                flow_concentration=sum(probability * probability for probability in probabilities),
                strongly_connected_regions=len(topology.regions),
                cyclic_regions=sum(region.cyclic for region in topology.regions),
                maximum_dependency_depth=topology.maximum_depth,
                entries=_rank_flow_symbols(entries[group_id], limit),
                exits=_rank_flow_symbols(exits[group_id], limit),
                hubs=_rank_flow_symbols(hubs[group_id], limit),
                bridges=topology.bridges[:limit],
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
    channels: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ProgramGroupProfile:
    """Flow, topology, and boundary facts for one fitted program-map group."""

    group_id: int
    cluster_ids: tuple[int, ...]
    symbols: tuple[str, ...]
    files: tuple[str, ...]
    packages: tuple[str, ...]
    raw_channel_mix: tuple[tuple[str, int], ...]
    weighted_channel_mix: tuple[tuple[str, float], ...]
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
class ProgramMapPartitionQuality:
    """Reader-facing quality facts for the exact bounded program-map partition."""

    group_count: int
    minimum_group_flow: float
    maximum_group_flow: float
    flow_imbalance: float
    mean_cohesion: float
    mean_coupling: float
    mean_entropy: float
    cyclic_group_count: int
    boundary_symbol_count: int


@dataclass(frozen=True)
class ProgramMapPartitionDrift:
    """Membership continuity facts comparing two bounded program-map partitions."""

    retained_clusters: int
    moved_clusters: int
    added_clusters: int
    removed_clusters: int
    unchanged_groups: int
    split_groups: int
    merged_groups: int
    overlaps: tuple[ProgramMapPartitionOverlap, ...] = ()

    @property
    def has_membership_change(self) -> bool:
        return bool(self.moved_clusters or self.added_clusters or self.removed_clusters)


@dataclass(frozen=True)
class ProgramMapPartitionOverlap:
    """One non-empty predecessor/successor group intersection."""

    previous_group_id: int
    current_group_id: int
    shared_clusters: int
    previous_fraction: float
    current_fraction: float


def _partition_overlap_matrix(
    previous_groups: list[set[int]],
    current_groups: list[set[int]],
) -> tuple[ProgramMapPartitionOverlap, ...]:
    overlaps = []
    for previous_id, previous in enumerate(previous_groups):
        for current_id, current in enumerate(current_groups):
            shared = len(previous & current)
            if shared:
                overlaps.append(
                    ProgramMapPartitionOverlap(
                        previous_id,
                        current_id,
                        shared,
                        shared / len(previous),
                        shared / len(current),
                    )
                )
    return tuple(overlaps)


def _match_partition_groups(
    overlaps: tuple[ProgramMapPartitionOverlap, ...],
    previous_count: int,
    current_count: int,
) -> tuple[ProgramMapPartitionOverlap, ...]:
    """Find the maximum shared-membership matching for the bounded component budget."""
    candidates_by_previous: dict[int, tuple[ProgramMapPartitionOverlap, ...]] = {
        previous_id: tuple(
            sorted(
                (overlap for overlap in overlaps if overlap.previous_group_id == previous_id),
                key=lambda item: (-item.shared_clusters, -item.current_fraction, item.current_group_id),
            )
        )
        for previous_id in range(previous_count)
    }

    @lru_cache
    def match(previous_id: int, used_current: int) -> tuple[int, int, tuple[int, ...]]:
        if previous_id == previous_count:
            return 0, 0, ()
        shared, count, current_ids = match(previous_id + 1, used_current)
        best = shared, count, (-1, *current_ids)
        for overlap in candidates_by_previous[previous_id]:
            bit = 1 << overlap.current_group_id
            if used_current & bit:
                continue
            tail_shared, tail_count, tail_ids = match(previous_id + 1, used_current | bit)
            candidate = (
                overlap.shared_clusters + tail_shared,
                1 + tail_count,
                (overlap.current_group_id, *tail_ids),
            )
            if candidate[:2] > best[:2] or (candidate[:2] == best[:2] and candidate[2] < best[2]):
                best = candidate
        return best

    _, _, selected_ids = match(0, 0)
    by_pair = {(item.previous_group_id, item.current_group_id): item for item in overlaps}
    return tuple(
        by_pair[previous_id, current_id] for previous_id, current_id in enumerate(selected_ids) if current_id >= 0
    )


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
    information: ProgramMapInformation = field(default_factory=lambda: ProgramMapInformation((), ()))
    channels: ProgramMapChannelAnalysis | None = None
    packages: ProgramMapPackageAnalysis | None = None
    hierarchy: ProgramMapHierarchyAnalysis | None = None
    quality: ProgramMapPartitionQuality | None = None

    def group_flow(self, group: set[int]) -> float:
        return sum(self.node_flow.get(cluster_id, 0.0) for cluster_id in group)

    def group_profile(self, group: set[int]) -> ProgramGroupProfile:
        """Return the profile for the exact fitted group identity."""
        identity = tuple(sorted(group))
        for profile in self.profiles:
            if profile.cluster_ids == identity:
                return profile
        raise KeyError(f"Unknown fitted ProgramMap group: {list(identity)}")


def _partition_quality(
    groups: list[set[int]],
    node_flow: dict[int, float],
    profiles: tuple[ProgramGroupProfile, ...],
) -> ProgramMapPartitionQuality:
    flows = [sum(node_flow.get(cluster_id, 0.0) for cluster_id in group) for group in groups]
    if not flows:
        return ProgramMapPartitionQuality(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)
    minimum = min(flows)
    maximum = max(flows)
    return ProgramMapPartitionQuality(
        group_count=len(groups),
        minimum_group_flow=minimum,
        maximum_group_flow=maximum,
        flow_imbalance=maximum - minimum,
        mean_cohesion=sum(profile.cohesion for profile in profiles) / len(profiles),
        mean_coupling=sum(profile.coupling for profile in profiles) / len(profiles),
        mean_entropy=sum(profile.flow_entropy for profile in profiles) / len(profiles),
        cyclic_group_count=sum(profile.cyclic_regions > 0 for profile in profiles),
        boundary_symbol_count=len({symbol for profile in profiles for symbol in profile.boundary_symbols}),
    )


def assess_program_map_partition(program_map: ProgramMap) -> ProgramMapPartitionQuality:
    """Calculate flow balance, cohesion, and boundary complexity for fitted groups."""
    return _partition_quality(program_map.groups, program_map.node_flow, program_map.profiles)


def compare_program_map_partitions(
    previous_groups: list[set[int]],
    current_groups: list[set[int]],
) -> ProgramMapPartitionDrift:
    """Measure membership continuity without treating reordered groups as changed code."""
    previous_owner = {cluster_id: index for index, group in enumerate(previous_groups) for cluster_id in group}
    current_owner = {cluster_id: index for index, group in enumerate(current_groups) for cluster_id in group}
    previous_clusters = set(previous_owner)
    current_clusters = set(current_owner)
    shared = previous_clusters & current_clusters
    overlaps = _partition_overlap_matrix(previous_groups, current_groups)
    matched = _match_partition_groups(overlaps, len(previous_groups), len(current_groups))
    retained_by_match = sum(overlap.shared_clusters for overlap in matched)
    previous_to_current: dict[int, set[int]] = defaultdict(set)
    current_to_previous: dict[int, set[int]] = defaultdict(set)
    for overlap in overlaps:
        previous_to_current[overlap.previous_group_id].add(overlap.current_group_id)
        current_to_previous[overlap.current_group_id].add(overlap.previous_group_id)
    unchanged = sum(
        set(previous_groups[overlap.previous_group_id]) == set(current_groups[overlap.current_group_id])
        for overlap in matched
    )
    return ProgramMapPartitionDrift(
        retained_clusters=len(shared),
        moved_clusters=len(shared) - retained_by_match,
        added_clusters=len(current_clusters - previous_clusters),
        removed_clusters=len(previous_clusters - current_clusters),
        unchanged_groups=unchanged,
        split_groups=sum(len(current_ids) > 1 for current_ids in previous_to_current.values()),
        merged_groups=sum(len(previous_ids) > 1 for previous_ids in current_to_previous.values()),
        overlaps=overlaps,
    )


def _partition_at_depth(module_paths: dict[int, tuple[int, ...]], depth: int) -> list[set[int]]:
    modules: dict[tuple[int, ...], set[int]] = defaultdict(set)
    for cluster_id, path in module_paths.items():
        modules[path[: min(depth, len(path))]].add(cluster_id)
    return list(modules.values())


@dataclass(frozen=True)
class ProgramMapHierarchyCandidate:
    """One evaluated Infomap hierarchy cut before and after budget fitting."""

    depth: int
    natural_groups: tuple[tuple[int, ...], ...]
    groups: tuple[tuple[int, ...], ...]
    codelength: float
    compression: float
    fitness: ProgramMapHierarchyFitness

    @property
    def natural_group_count(self) -> int:
        return len(self.natural_groups)

    @property
    def group_count(self) -> int:
        return len(self.groups)


@dataclass(frozen=True)
class ProgramMapHierarchyFitness:
    """Flow retention and size facts used to explain a hierarchy cut."""

    internal_weight: float
    crossing_weight: float
    internal_ratio: float
    largest_group_fraction: float
    singleton_group_count: int
    budget_distance: int


@dataclass(frozen=True)
class ProgramMapHierarchyAnalysis:
    """All hierarchy cuts and the deterministic cut selected for component naming."""

    candidates: tuple[ProgramMapHierarchyCandidate, ...]
    selected_depth: int

    @property
    def selected(self) -> ProgramMapHierarchyCandidate:
        return next(candidate for candidate in self.candidates if candidate.depth == self.selected_depth)

    @property
    def selection_summary(self) -> str:
        """Give the agent layer a deterministic reason for the selected hierarchy cut."""
        selected = self.selected
        return (
            f"depth {selected.depth}: {selected.natural_group_count} natural modules, "
            f"{selected.group_count} fitted modules, {selected.fitness.internal_ratio:.1%} retained flow"
        )


def _hierarchy_fitness(
    meta_graph: nx.DiGraph,
    groups: list[set[int]],
    low: int,
    high: int,
) -> ProgramMapHierarchyFitness:
    owner = {cluster_id: group_id for group_id, group in enumerate(groups) for cluster_id in group}
    internal = crossing = 0.0
    for source, destination, attrs in meta_graph.edges(data=True):
        weight = float(attrs.get("weight", 1.0))
        if owner[source] == owner[destination]:
            internal += weight
        else:
            crossing += weight
    sizes = [len(group) for group in groups]
    total_size = sum(sizes)
    count = len(groups)
    budget_distance = low - count if count < low else count - high if count > high else 0
    return ProgramMapHierarchyFitness(
        internal_weight=internal,
        crossing_weight=crossing,
        internal_ratio=internal / (internal + crossing) if internal + crossing else 0.0,
        largest_group_fraction=max(sizes, default=0) / total_size if total_size else 0.0,
        singleton_group_count=sum(size == 1 for size in sizes),
        budget_distance=budget_distance,
    )


def analyze_program_map_hierarchy(
    module_paths: dict[int, tuple[int, ...]],
    meta_graph: nx.DiGraph,
    cluster_result: ClusterResult,
    low: int,
    high: int,
    seed: int,
) -> ProgramMapHierarchyAnalysis:
    """Score every Infomap hierarchy cut while preserving the component-count contract."""
    max_depth = max((len(path) for path in module_paths.values()), default=1)
    method_count = _method_counts(cluster_result)
    candidates = []
    for depth in range(1, max_depth + 1):
        natural = _partition_at_depth(module_paths, depth)
        groups = _fit_partition_to_budget(natural, meta_graph, cluster_result, method_count, low, high)
        codelength, compression = _score_program_partition(meta_graph, groups, seed)
        candidates.append(
            ProgramMapHierarchyCandidate(
                depth=depth,
                natural_groups=tuple(tuple(sorted(group)) for group in natural),
                groups=tuple(tuple(sorted(group)) for group in groups),
                codelength=codelength,
                compression=compression,
                fitness=_hierarchy_fitness(meta_graph, groups, low, high),
            )
        )
    in_range = [candidate for candidate in candidates if low <= candidate.natural_group_count <= high]
    if in_range:
        return ProgramMapHierarchyAnalysis(tuple(candidates), in_range[0].depth)

    def distance(candidate: ProgramMapHierarchyCandidate) -> tuple[int, int, int]:
        count = candidate.natural_group_count
        outside = low - count if count < low else count - high
        return outside, -count, candidate.depth

    return ProgramMapHierarchyAnalysis(tuple(candidates), min(candidates, key=distance).depth)


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
    information = build_program_map_information(cfg_graph)
    channels = information.channel_analysis()
    packages = analyze_program_map_packages(information)
    n_leaf = meta_graph.number_of_nodes()
    if n_leaf == 0:
        return ProgramMap(
            groups=[],
            node_flow={},
            module_paths={},
            codelength=0.0,
            compression=0.0,
            hierarchy_levels=0,
            information=information,
            channels=channels,
            packages=packages,
            quality=_partition_quality([], {}, ()),
        )
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
        profiles = build_program_map_profiles(cluster_result, cfg_graph, groups, information=information)
        return ProgramMap(
            groups=groups,
            node_flow=node_flow,
            module_paths=isolated_paths,
            codelength=0.0,
            compression=0.0,
            hierarchy_levels=1,
            profiles=profiles,
            information=information,
            channels=channels,
            packages=packages,
            quality=_partition_quality(groups, node_flow, profiles),
        )

    result = infomap.run(meta_graph, directed=True, seed=seed, num_trials=INFOMAP_TRIALS)
    module_paths = {int(cluster_id): path for cluster_id, path in result.multilevel_modules().items()}
    node_flow = {int(node.node_id): node.flow for node in result.nodes()}
    if n_leaf <= low:
        groups = [{cid} for cid in sorted(meta_graph.nodes)]
        hierarchy = None
    else:
        hierarchy = analyze_program_map_hierarchy(module_paths, meta_graph, cluster_result, low, high, seed)
        groups = [set(group) for group in hierarchy.selected.groups]
    codelength, compression = (
        _score_program_partition(meta_graph, groups, seed)
        if hierarchy is None
        else (
            hierarchy.selected.codelength,
            hierarchy.selected.compression,
        )
    )
    profiles = build_program_map_profiles(cluster_result, cfg_graph, groups, information=information)
    program_map = ProgramMap(
        groups=groups,
        node_flow=node_flow,
        module_paths=module_paths,
        codelength=codelength,
        compression=compression,
        hierarchy_levels=max((len(path) for path in module_paths.values()), default=0),
        profiles=profiles,
        information=information,
        channels=channels,
        packages=packages,
        hierarchy=hierarchy,
        quality=_partition_quality(groups, node_flow, profiles),
    )
    logger.info(
        f"[ProgramMap] {meta_graph.number_of_nodes()} leaf clusters -> {len(groups)} flow modules "
        f"(codelength={program_map.codelength:.4f}, compression={program_map.compression:.1%}, "
        f"hierarchy={program_map.hierarchy_levels}, sizes {sorted((len(g) for g in groups), reverse=True)}, "
        f"{program_map.hierarchy.selection_summary if program_map.hierarchy else 'leaf-level partition'})"
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


@dataclass(frozen=True)
class ProgramMapLineageClaim:
    """One predecessor component's weighted claim on a successor group."""

    successor_index: int
    predecessor_id: str
    shared_methods: int
    successor_methods: int

    @property
    def overlap(self) -> float:
        return self.shared_methods / self.successor_methods if self.successor_methods else 0.0


@dataclass(frozen=True)
class ProgramMapLineage:
    """Deterministic ownership reconciliation after a fresh program-map partition."""

    owners: tuple[str, ...]
    claims: tuple[ProgramMapLineageClaim, ...]
    retired_owners: tuple[str, ...]


def reconcile_program_map_lineage(
    groups: list[set[int]],
    previous_owner: dict[int, str],
    method_count: dict[int, int],
) -> ProgramMapLineage:
    """Carry the strongest predecessor identity into each fresh flow module once."""
    candidates: list[ProgramMapLineageClaim] = []
    previous_ids = set(previous_owner.values()) - {""}
    for index, group in enumerate(groups):
        claims: Counter[str] = Counter()
        successor_methods = sum(method_count.get(cluster_id, 0) for cluster_id in group)
        for cluster_id in group:
            if owner := previous_owner.get(cluster_id):
                claims[owner] += method_count.get(cluster_id, 0)
        candidates.extend(
            ProgramMapLineageClaim(index, owner, shared, successor_methods) for owner, shared in claims.items()
        )
    owner_ids = tuple(sorted(previous_ids))
    owner_bits = {owner: 1 << index for index, owner in enumerate(owner_ids)}
    claims_by_successor: dict[int, tuple[ProgramMapLineageClaim, ...]] = {
        index: tuple(
            sorted(
                (claim for claim in candidates if claim.successor_index == index and claim.shared_methods > 0),
                key=lambda item: (-item.shared_methods, -item.overlap, item.predecessor_id),
            )
        )
        for index in range(len(groups))
    }

    @lru_cache
    def optimal_assignment(
        successor_index: int,
        assigned_mask: int,
    ) -> tuple[int, float, int, tuple[str, ...]]:
        if successor_index == len(groups):
            return 0, 0.0, 0, ()
        options: list[ProgramMapLineageClaim | None] = [None, *claims_by_successor[successor_index]]
        best: tuple[int, float, int, tuple[str, ...]] | None = None
        for claim in options:
            if claim is not None and assigned_mask & owner_bits[claim.predecessor_id]:
                continue
            next_mask = assigned_mask if claim is None else assigned_mask | owner_bits[claim.predecessor_id]
            shared, overlap, count, tail = optimal_assignment(successor_index + 1, next_mask)
            candidate = (
                shared + (claim.shared_methods if claim else 0),
                overlap + (claim.overlap if claim else 0.0),
                count + int(claim is not None),
                ((claim.predecessor_id if claim else ""), *tail),
            )
            if best is None or candidate[:3] > best[:3] or (candidate[:3] == best[:3] and candidate[3] < best[3]):
                best = candidate
        assert best is not None
        return best

    _, _, _, owners = optimal_assignment(0, 0)
    assigned = {owner for owner in owners if owner}
    return ProgramMapLineage(
        owners=owners,
        claims=tuple(sorted(candidates, key=lambda item: (item.successor_index, item.predecessor_id))),
        retired_owners=tuple(sorted(previous_ids - assigned)),
    )


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
    return list(reconcile_program_map_lineage(groups, previous_owner, method_count).owners)


@dataclass(frozen=True)
class AnchoredGrouping:
    """A grouping carried forward from the previous run, plus what it cost."""

    groups: list[set[int]]
    #: index into ``groups`` -> the component id it inherited, or "" when new.
    owners: list[str]
    #: True when drift forced a from-scratch re-derivation rather than a carry-forward.
    regrouped: bool
    #: Membership continuity between the carried and freshly fitted partitions.
    partition_drift: ProgramMapPartitionDrift | None = None


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

    partition_drift = compare_program_map_partitions(groups, fresh_groups)

    effective_high = min(high, len(live))
    if len(groups) > effective_high:
        logger.info(
            f"[Anchored] carried grouping has {len(groups)} components above "
            f"the {effective_high} maximum; re-deriving structure from ProgramMap"
        )
        return AnchoredGrouping(
            fresh_groups,
            _inherit_ids(fresh_groups, previous_owner, method_count),
            True,
            partition_drift,
        )

    _, carried_compression = _score_program_partition(meta_graph, groups, seed)
    if fresh_map.compression - carried_compression > drift_budget:
        logger.info(
            f"[Anchored] carried compression {carried_compression:.1%} vs "
            f"{fresh_map.compression:.1%} fresh (> {drift_budget:.1%} budget); "
            "re-deriving structure from ProgramMap"
        )
        return AnchoredGrouping(
            fresh_groups,
            _inherit_ids(fresh_groups, previous_owner, method_count),
            True,
            partition_drift,
        )

    logger.info(
        f"[Anchored] {len(live)} leaf clusters -> {len(groups)} components carried forward "
        f"({len(new_subsystems)} new component(s), {len(absorbed)} clusters absorbed, "
        f"compression={carried_compression:.1%} vs {fresh_map.compression:.1%} fresh, "
        f"continuity={partition_drift.retained_clusters - partition_drift.moved_clusters}/"
        f"{partition_drift.retained_clusters} retained)"
    )
    return AnchoredGrouping(groups, owners, False, partition_drift)
