"""Queryable views over authoritative program facts."""

from static_analyzer.program_info.models import Channel, EdgeEvidence, ProgramInformation, SymbolFact


def symbol(information: ProgramInformation, qualified_name: str) -> SymbolFact:
    try:
        return information._symbol_index[qualified_name]
    except KeyError as exc:
        raise KeyError(f"Unknown program symbol: {qualified_name}") from exc


def evidence(
    information: ProgramInformation, qualified_name: str, *, incoming: bool, channels: set[Channel] | None = None
) -> tuple[EdgeEvidence, ...]:
    symbol(information, qualified_name)
    selected = (
        edge
        for edge in information.edges
        if (edge.destination if incoming else edge.source) == qualified_name
        and (channels is None or edge.channel in channels)
    )
    return tuple(selected)


def scoped(information: ProgramInformation, names: set[str]) -> ProgramInformation:
    missing = names - information._symbol_index.keys()
    if missing:
        raise KeyError(f"Unknown program symbols: {sorted(missing)}")
    symbols = tuple(item for item in information.symbols if item.qualified_name in names)
    edges = tuple(edge for edge in information.edges if edge.source in names and edge.destination in names)
    return ProgramInformation(symbols, edges)
