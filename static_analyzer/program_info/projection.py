"""Validated codec for program-information NetworkX projections."""

import math

import networkx as nx

from static_analyzer.program_info.errors import InvalidWeightError, ProgramInformationError
from static_analyzer.program_info.models import Channel, EdgeEvidence, ProgramInformation, SymbolFact

CODEC_VERSION = 1


def to_projection(information: ProgramInformation) -> nx.DiGraph:
    """Encode all authoritative facts and typed edge evidence deterministically."""
    graph = nx.DiGraph(program_information_codec=CODEC_VERSION)
    for fact in information.symbols:
        graph.add_node(
            fact.qualified_name,
            qualified_name=fact.qualified_name,
            kind=fact.kind,
            file_path=fact.file_path,
            line_start=fact.line_start,
            line_end=fact.line_end,
            col_start=fact.col_start,
            detail=fact.detail,
            selection_span=fact.selection_span,
            parent_chain=fact.parent_chain,
            tags=fact.tags,
            deprecated=fact.deprecated,
            visibility=fact.visibility,
            modifiers=fact.modifiers,
            annotations=fact.annotations,
            import_evidence=fact.import_evidence,
            type_use_evidence=fact.type_use_evidence,
        )
    grouped: dict[tuple[str, str], list[EdgeEvidence]] = {}
    for edge in information.edges:
        grouped.setdefault((edge.source, edge.destination), []).append(edge)
    for endpoints, evidence in sorted(grouped.items()):
        encoded = tuple((item.channel.value, item.count, item.raw_weight) for item in sorted(evidence))
        attrs: dict[str, object] = {"evidence": encoded, "weight": sum(item.weighted_value for item in evidence)}
        for item in evidence:
            attrs[item.channel.value] = item.weighted_value
            attrs[f"{item.channel.value}_count"] = item.count
        graph.add_edge(*endpoints, **attrs)
    return graph


def from_projection(graph: nx.DiGraph) -> ProgramInformation:
    """Decode a projection, rejecting partial or malformed authoritative attrs."""
    if not isinstance(graph, nx.DiGraph):
        raise ProgramInformationError("Program-information projection must be a directed graph")
    version = graph.graph.get("program_information_codec")
    if version not in (None, CODEC_VERSION):
        raise ProgramInformationError(f"Unsupported program-information codec version: {version}")
    symbols: list[SymbolFact] = []
    for name, attrs in sorted(graph.nodes(data=True), key=lambda item: str(item[0])):
        required = {"file_path"} if version is None else {"file_path", "line_start", "line_end"}
        missing = required - attrs.keys()
        if missing:
            raise ProgramInformationError(f"Symbol {name!r} is missing projection attrs: {sorted(missing)}")
        qualified_name = attrs.get("qualified_name", name)
        kind = attrs.get("kind", attrs.get("type", 0 if version is None else None))
        if not isinstance(qualified_name, str) or not isinstance(kind, int):
            raise ProgramInformationError(f"Symbol {name!r} has malformed identity or kind")
        try:
            symbols.append(
                SymbolFact(
                    qualified_name,
                    kind,
                    str(attrs["file_path"]),
                    int(attrs.get("line_start", 0)),
                    int(attrs.get("line_end", 0)),
                    int(attrs.get("col_start", 0)),
                    str(attrs.get("detail", "")),
                    tuple(attrs.get("selection_span", (0, 0, 0, 0))),
                    tuple(tuple(parent) for parent in attrs.get("parent_chain", ())),
                    tuple(attrs.get("tags", ())),
                    bool(attrs.get("deprecated", False)),
                    str(attrs.get("visibility", "unknown")),
                    tuple(attrs.get("modifiers", ())),
                    tuple(attrs.get("annotations", ())),
                    tuple(attrs.get("import_evidence", ())),
                    tuple(attrs.get("type_use_evidence", ())),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ProgramInformationError(f"Symbol {name!r} has malformed projection attrs") from exc
    edges: list[EdgeEvidence] = []
    for source, destination, attrs in sorted(graph.edges(data=True)):
        encoded = attrs.get("evidence")
        if encoded is None:
            encoded = _decode_legacy_evidence(source, destination, attrs)
        if not isinstance(encoded, (tuple, list)):
            raise ProgramInformationError(f"Edge {source!r} -> {destination!r} has malformed evidence")
        for item in encoded:
            try:
                channel_name, count, raw_weight = item
                if not isinstance(count, int) or not isinstance(raw_weight, (int, float)):
                    raise TypeError
                edges.append(
                    EdgeEvidence(str(source), str(destination), Channel(channel_name), count, float(raw_weight))
                )
            except (TypeError, ValueError) as exc:
                raise ProgramInformationError(f"Edge {source!r} -> {destination!r} has malformed evidence") from exc
    return ProgramInformation(tuple(sorted(symbols)), tuple(sorted(edges)))


def _decode_legacy_evidence(source: str, destination: str, attrs: dict) -> tuple[tuple[str, int, float], ...]:
    """Decode explicit legacy channel attrs; an untyped weight is not trustworthy."""
    result = []
    for channel in Channel:
        if channel.value not in attrs:
            continue
        value = attrs[channel.value]
        count = attrs.get(f"{channel.value}_count", 1)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or not isinstance(count, int):
            raise InvalidWeightError(f"Invalid legacy {channel.value} evidence {source} -> {destination}")
        raw = float(value) / (max(1, count) if channel == Channel.CALL else 1.0)
        raw /= (
            1.0
            if channel == Channel.CALL
            else {Channel.CONTAINS: 1, Channel.INHERITS: 1.25, Channel.TYPEREF: 0.5, Channel.IMPORT: 0.25}[channel]
        )
        result.append((channel.value, count, raw))
    if not result:
        if not attrs:
            return ((Channel.CALL.value, 1, 1.0),)
        weight = attrs.get("weight")
        if isinstance(weight, (int, float)) and math.isfinite(weight) and weight >= 0:
            return ((Channel.CALL.value, 1, float(weight)),)
        raise ProgramInformationError(f"Edge {source!r} -> {destination!r} has no typed evidence")
    return tuple(result)
