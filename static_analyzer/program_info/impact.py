"""Bounded deterministic summaries of incremental program-information deltas."""

from collections import deque
from dataclasses import dataclass

from static_analyzer.program_info.models import Channel, ProgramDelta, ProgramInformation


@dataclass(frozen=True)
class ImpactedSymbol:
    qualified_name: str
    depth: int
    directions: tuple[str, ...]
    channels: tuple[Channel, ...]


@dataclass(frozen=True)
class ProgramDeltaSummary:
    added_count: int = 0
    removed_count: int = 0
    changed_count: int = 0
    added_edge_count: int = 0
    removed_edge_count: int = 0
    changed_edge_count: int = 0
    changed_channels: tuple[Channel, ...] = ()
    added_symbols: tuple[str, ...] = ()
    removed_symbols: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    impacted_symbols: tuple[ImpactedSymbol, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.added_count,
                self.removed_count,
                self.changed_count,
                self.added_edge_count,
                self.removed_edge_count,
                self.changed_edge_count,
            )
        )

    def llm_str(self) -> str:
        """Render bounded facts without exposing an additional public schema."""
        if self.is_empty:
            return ""
        parts = [
            f"program delta: symbols +{self.added_count}/-{self.removed_count}/~{self.changed_count}",
            f"evidence +{self.added_edge_count}/-{self.removed_edge_count}/~{self.changed_edge_count}",
        ]
        if self.changed_channels:
            parts.append("channels " + ", ".join(channel.value for channel in self.changed_channels))
        for label, names in (
            ("added", self.added_symbols),
            ("removed", self.removed_symbols),
            ("changed", self.changed_symbols),
        ):
            if names:
                parts.append(f"{label}: {', '.join(names)}")
        if self.impacted_symbols:
            parts.append(
                "impact: "
                + ", ".join(
                    f"{item.qualified_name}@{item.depth}[{'/'.join(item.directions)}:{'/'.join(c.value for c in item.channels)}]"
                    for item in self.impacted_symbols
                )
            )
        return "; ".join(parts)


def summarize_delta(
    delta: ProgramDelta,
    old: ProgramInformation,
    new: ProgramInformation,
    *,
    max_depth: int = 2,
    limit: int = 12,
    symbol_limit: int = 5,
) -> ProgramDeltaSummary:
    """Summarize changes and traverse typed evidence around changed symbols."""
    if max_depth < 0 or limit < 0 or symbol_limit < 0:
        raise ValueError("Program-delta bounds must be non-negative")
    changed_edges = delta.added_edges + delta.removed_edges + delta.changed_edges
    channels = tuple(sorted({channel for _, _, channel in changed_edges}))
    seeds = set(delta.added_symbols) | set(delta.removed_symbols) | set(delta.changed_symbols)
    seeds.update(endpoint for edge in changed_edges for endpoint in edge[:2])
    impacted = _traverse_impact(old, new, seeds, max_depth, limit)
    return ProgramDeltaSummary(
        len(delta.added_symbols),
        len(delta.removed_symbols),
        len(delta.changed_symbols),
        len(delta.added_edges),
        len(delta.removed_edges),
        len(delta.changed_edges),
        channels,
        delta.added_symbols[:symbol_limit],
        delta.removed_symbols[:symbol_limit],
        delta.changed_symbols[:symbol_limit],
        impacted,
    )


def _traverse_impact(
    old: ProgramInformation, new: ProgramInformation, seeds: set[str], max_depth: int, limit: int
) -> tuple[ImpactedSymbol, ...]:
    adjacency: dict[str, list[tuple[str, str, Channel]]] = {}
    for edge in sorted(set(old.edges) | set(new.edges)):
        adjacency.setdefault(edge.source, []).append((edge.destination, "out", edge.channel))
        adjacency.setdefault(edge.destination, []).append((edge.source, "in", edge.channel))
    queue = deque((seed, 0) for seed in sorted(seeds))
    visited = set(seeds)
    facts: dict[tuple[str, int], tuple[set[str], set[Channel]]] = {}
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor, direction, channel in sorted(
            adjacency.get(current, []), key=lambda item: (item[0], item[1], item[2])
        ):
            if neighbor in seeds:
                continue
            key = (neighbor, depth + 1)
            directions, channels = facts.setdefault(key, (set(), set()))
            directions.add(direction)
            channels.add(channel)
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
    ranked = sorted(facts.items(), key=lambda item: (item[0][1], item[0][0]))[:limit]
    return tuple(
        ImpactedSymbol(name, depth, tuple(sorted(directions)), tuple(sorted(channels)))
        for (name, depth), (directions, channels) in ranked
    )
