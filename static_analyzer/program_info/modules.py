"""Exact and scoped module partition analysis."""

from collections import Counter
from dataclasses import dataclass

from static_analyzer.program_info.errors import InvalidClusterCoverError
from static_analyzer.program_info.flow import FlowFacts, analyze_flow
from static_analyzer.program_info.models import Channel, ProgramInformation


@dataclass(frozen=True)
class ModuleFlow:
    source_module: int
    destination_module: int
    weight: float
    channels: tuple[tuple[Channel, float], ...]


@dataclass(frozen=True)
class ModuleProfile:
    module_id: int
    members: tuple[str, ...]
    files: tuple[str, ...]
    packages: tuple[str, ...]
    flow: FlowFacts
    entry_symbols: tuple[str, ...]
    exit_symbols: tuple[str, ...]
    boundary_symbols: tuple[str, ...]
    cohesion: float
    coupling: float


@dataclass(frozen=True)
class ModuleAnalysis:
    profiles: tuple[ModuleProfile, ...]
    inter_module_flow: tuple[ModuleFlow, ...]


def analyze_modules(
    information: ProgramInformation, partition: dict[int, set[str]], *, exact: bool = True, limit: int = 8
) -> ModuleAnalysis:
    owner: dict[str, int] = {}
    known = {symbol.qualified_name for symbol in information.symbols}
    for module_id, members in sorted(partition.items()):
        unknown = members - known
        duplicate = members & owner.keys()
        if unknown or duplicate:
            raise InvalidClusterCoverError(
                f"Module {module_id} has unknown={sorted(unknown)} duplicate={sorted(duplicate)}"
            )
        owner.update((member, module_id) for member in members)
    if exact and known != owner.keys():
        raise InvalidClusterCoverError(f"Module cover omits {sorted(known - owner.keys())}")

    crossing: dict[tuple[int, int], Counter[Channel]] = {}
    boundary: dict[int, set[str]] = {module_id: set() for module_id in partition}
    incoming: dict[int, Counter[str]] = {module_id: Counter() for module_id in partition}
    outgoing: dict[int, Counter[str]] = {module_id: Counter() for module_id in partition}
    for edge in information.edges:
        source_module, target_module = owner.get(edge.source), owner.get(edge.destination)
        if source_module is None or target_module is None or source_module == target_module:
            continue
        crossing.setdefault((source_module, target_module), Counter())[edge.channel] += edge.weighted_value
        boundary[source_module].add(edge.source)
        boundary[target_module].add(edge.destination)
        outgoing[source_module][edge.source] += edge.weighted_value
        incoming[target_module][edge.destination] += edge.weighted_value
    facts = {symbol.qualified_name: symbol for symbol in information.symbols}
    profiles = []
    for module_id, members in sorted(partition.items()):
        flow = analyze_flow(information.edges, members, limit)
        possible = len(members) * max(1, len(members) - 1)
        rank = lambda values: tuple(name for name, _ in sorted(values.items(), key=lambda x: (-x[1], x[0]))[:limit])
        total = flow.internal_weight + flow.crossing_weight
        profiles.append(
            ModuleProfile(
                module_id,
                tuple(sorted(members)),
                tuple(sorted({facts[name].file_path for name in members})),
                tuple(sorted({facts[name].package for name in members})),
                flow,
                rank(incoming[module_id]),
                rank(outgoing[module_id]),
                tuple(sorted(boundary[module_id]))[:limit],
                flow.internal_weight / possible,
                flow.crossing_weight / total if total else 0.0,
            )
        )
    flows = tuple(
        ModuleFlow(source, target, sum(channels.values()), tuple(sorted(channels.items())))
        for (source, target), channels in sorted(crossing.items())
    )
    return ModuleAnalysis(tuple(profiles), flows)
