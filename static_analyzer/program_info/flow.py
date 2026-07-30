"""Bounded weighted-flow facts."""

import math
from collections import Counter
from dataclasses import dataclass

from static_analyzer.program_info.models import Channel, EdgeEvidence


@dataclass(frozen=True)
class FlowFacts:
    total_weight: float
    internal_weight: float
    crossing_weight: float
    internal_ratio: float
    entropy: float
    concentration: float
    channel_mix: tuple[tuple[Channel, float], ...]
    top_incoming: tuple[tuple[str, float], ...]
    top_outgoing: tuple[tuple[str, float], ...]


def analyze_flow(edges: tuple[EdgeEvidence, ...], members: set[str], limit: int = 8) -> FlowFacts:
    """Summarize flow touching a scope; unrelated edges are excluded."""
    incoming: Counter[str] = Counter()
    outgoing: Counter[str] = Counter()
    channels: Counter[Channel] = Counter()
    internal = crossing = 0.0
    weights: list[float] = []
    for edge in edges:
        source_inside, target_inside = edge.source in members, edge.destination in members
        if not source_inside and not target_inside:
            continue
        value = edge.weighted_value
        weights.append(value)
        channels[edge.channel] += value
        if source_inside and target_inside:
            internal += value
        else:
            crossing += value
        if source_inside:
            outgoing[edge.source] += value
        if target_inside:
            incoming[edge.destination] += value
    total = internal + crossing
    probabilities = [weight / sum(weights) for weight in weights] if weights and sum(weights) else []
    entropy = -sum(p * math.log2(p) for p in probabilities)
    concentration = sum(p * p for p in probabilities)
    rank = lambda values: tuple(sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit])
    return FlowFacts(
        total,
        internal,
        crossing,
        internal / total if total else 0.0,
        entropy,
        concentration,
        tuple(sorted(channels.items())),
        rank(incoming),
        rank(outgoing),
    )
