"""Build program information from the authoritative call graph."""

from collections import defaultdict

from static_analyzer.program_info.models import Channel, EdgeEvidence, ProgramInformation, SymbolFact


def build_program_information(call_graph: object) -> ProgramInformation:
    nodes = getattr(call_graph, "nodes")
    resolve = getattr(call_graph, "_resolve_name")
    symbols = tuple(
        sorted(
            SymbolFact(
                qualified_name=name,
                kind=int(node.type),
                file_path=node.file_path,
                line_start=node.line_start,
                line_end=node.line_end,
                col_start=getattr(node, "col_start", 0),
                detail=getattr(node, "detail", ""),
                selection_span=getattr(node, "selection_span", (0, 0, 0, 0)),
                parent_chain=tuple(getattr(node, "parent_chain", ())),
                tags=tuple(getattr(node, "tags", ())),
                deprecated=getattr(node, "deprecated", False),
                visibility=getattr(node, "visibility", "unknown"),
                modifiers=tuple(getattr(node, "modifiers", ())),
                annotations=tuple(
                    sorted(getattr(item, "name", str(item)) for item in getattr(node, "annotations", ()))
                ),
                import_evidence=tuple(
                    sorted(getattr(item.evidence, "spelling", "") for item in getattr(node, "import_evidence", ()))
                ),
                type_use_evidence=tuple(
                    sorted(getattr(item.evidence, "spelling", "") for item in getattr(node, "type_use_evidence", ()))
                ),
            )
            for name, node in nodes.items()
        )
    )
    aggregate: dict[tuple[str, str, Channel], tuple[int, float]] = defaultdict(lambda: (0, 0.0))
    for edge in getattr(call_graph, "edges"):
        key = (edge.get_source(), edge.get_destination(), Channel.CALL)
        count = getattr(edge, "call_site_count", len(getattr(edge, "_call_sites", ())))
        old_count, old_weight = aggregate[key]
        aggregate[key] = old_count + max(1, count), old_weight + max(1, count)
    for source, destination, raw_channel in getattr(call_graph, "reference_edges", ()):
        source, destination = resolve(source), resolve(destination)
        channel = Channel(raw_channel)
        key = source, destination, channel
        old_count, old_weight = aggregate[key]
        aggregate[key] = old_count + 1, old_weight + 1.0
    edges = tuple(
        sorted(
            EdgeEvidence(source, destination, channel, count, raw_weight)
            for (source, destination, channel), (count, raw_weight) in aggregate.items()
        )
    )
    return ProgramInformation(symbols, edges)
