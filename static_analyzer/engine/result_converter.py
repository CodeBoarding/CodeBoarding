"""Convert LSP engine output into ProgramGraph."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from static_analyzer.constants import GRAPH_NODE_TYPES, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import LanguageAnalysisResult, SymbolInfo
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.program_graph import (
    ProgramEdge,
    ProgramEdgeKind,
    ProgramGraph,
    ProgramNode,
    ProgramNodeKind,
    ProgramOccurrence,
    external_package_node_id,
    file_node_id,
    package_node_id,
    package_parent,
)

logger = logging.getLogger(__name__)

SymbolLocation = tuple[str, int, int, int, int]


def convert_to_codeboarding_format(
    symbol_table: SymbolTable,
    result: LanguageAnalysisResult,
    adapter: LanguageAdapter,
    project_root: Path | None = None,
) -> dict:
    """Convert one language analysis into its canonical ProgramGraph."""
    language = adapter.language
    if project_root is None:
        source_paths = [Path(os.path.abspath(path)) for path in result.source_files]
        common_path = Path(os.path.commonpath([str(path) for path in source_paths])) if source_paths else Path.cwd()
        project_root = common_path.parent if common_path in source_paths else common_path
    project_root = Path(os.path.abspath(project_root))
    program_graph = ProgramGraph(language=language.lower())

    edge_participants: set[str] = set()
    for edge in result.cfg.edges:
        edge_participants.add(edge.source)
        edge_participants.add(edge.destination)
    primary_qnames = {item.qualified_name for items in symbol_table.primary_file_symbols.values() for item in items}

    included_symbols: dict[str, SymbolInfo] = {}
    aliases_by_location: dict[SymbolLocation, list[str]] = {}
    for qname, sym in sorted(symbol_table.symbols.items()):
        node_type = _map_symbol_kind(sym.kind)
        is_reference = (
            qname in primary_qnames
            and adapter.is_reference_worthy(sym.kind)
            and not symbol_table.is_local_variable(sym)
        )
        if node_type not in GRAPH_NODE_TYPES and qname not in edge_participants and not is_reference:
            continue
        included_symbols[qname] = sym
        location = (
            str(sym.file_path),
            sym.start_line,
            sym.end_line,
            node_type.value,
            sym.start_char,
        )
        aliases_by_location.setdefault(location, []).append(qname)

    canonical_by_qname: dict[str, str] = {}
    for aliases in aliases_by_location.values():
        primary_aliases = [alias for alias in aliases if alias in primary_qnames]
        canonical = max(primary_aliases or aliases, key=lambda qname: (len(qname), qname))
        canonical_by_qname.update({alias: canonical for alias in aliases})

    for aliases in aliases_by_location.values():
        canonical = canonical_by_qname[aliases[0]]
        sym = included_symbols[canonical]
        reference_worthy = any(
            alias in primary_qnames
            and adapter.is_reference_worthy(included_symbols[alias].kind)
            and not symbol_table.is_local_variable(included_symbols[alias])
            for alias in aliases
        )
        program_graph.add_node(
            ProgramNode(
                node_id=canonical,
                kind=ProgramNodeKind.SYMBOL,
                language=language.lower(),
                name=sym.name,
                file_path=str(sym.file_path),
                symbol_type=_map_symbol_kind(sym.kind),
                line_start=sym.start_line + 1,
                line_end=sym.end_line + 1,
                col_start=sym.start_char,
                reference_worthy=reference_worthy,
                metadata={"aliases": sorted(alias for alias in aliases if alias != canonical)},
            )
        )
        # Register aliases in the graph map so resolve_symbol_id/has_symbol can
        # follow a non-canonical name from a prior result to its canonical node.
        for alias in aliases:
            if alias != canonical:
                program_graph._aliases[alias] = canonical

    edges_added = 0
    edges_skipped = 0
    for edge in result.cfg.edges:
        source = canonical_by_qname.get(edge.source)
        target = canonical_by_qname.get(edge.destination)
        if source is None or target is None:
            edges_skipped += 1
            continue
        program_graph.add_edge(
            ProgramEdge(
                kind=ProgramEdgeKind.CALL,
                source=source,
                target=target,
                occurrences=[
                    ProgramOccurrence(file=site.file, line=site.line, column=site.column) for site in edge.call_sites
                ],
            )
        )
        edges_added += 1

    logger.info(
        "Converted %d nodes, %d edges (%d skipped) for %s",
        len(program_graph.symbol_nodes()),
        edges_added,
        edges_skipped,
        language,
    )

    file_package: dict[str, str] = {}
    for file_path_str in sorted(result.source_files):
        file_path = Path(file_path_str).resolve()
        file_id = file_node_id(str(file_path))
        program_graph.add_node(
            ProgramNode(
                node_id=file_id,
                kind=ProgramNodeKind.FILE,
                language=language.lower(),
                name=str(file_path),
                file_path=str(file_path),
            )
        )
        package = adapter.get_package_for_file(file_path, project_root)
        if not isinstance(package, str) or not package:
            try:
                rel = file_path.relative_to(project_root)
                package = ".".join(rel.parent.parts) if rel.parent.parts else rel.stem
            except ValueError:
                package = file_path.parent.name or file_path.stem
        file_package[str(file_path)] = package
        package_id = package_node_id(language, package)
        program_graph.add_node(
            ProgramNode(
                node_id=package_id,
                kind=ProgramNodeKind.PACKAGE,
                language=language.lower(),
                name=package,
            )
        )
        program_graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, package_id, file_id))

    packages = sorted(set(file_package.values()))
    for package in packages:
        parent = package_parent(package)
        if parent and parent in packages:
            program_graph.add_edge(
                ProgramEdge(
                    ProgramEdgeKind.CONTAINS,
                    package_node_id(language, parent),
                    package_node_id(language, package),
                )
            )

    graph_symbol_ids = {node.id for node in program_graph.symbol_nodes() if node.id in symbol_table.symbols}
    containment_symbols = [symbol_table.symbols[qname] for qname in sorted(graph_symbol_ids)]
    by_file: dict[str, list] = {}
    for symbol in containment_symbols:
        by_file.setdefault(str(symbol.file_path), []).append(symbol)
    for symbols in by_file.values():
        for symbol in symbols:
            candidates = [
                parent
                for parent in symbols
                if parent.qualified_name != symbol.qualified_name
                and (parent.start_line, parent.start_char) <= (symbol.start_line, symbol.start_char)
                and (parent.end_line, parent.end_char) >= (symbol.end_line, symbol.end_char)
            ]
            semantic_candidates = [
                parent
                for parent in symbols
                if parent.qualified_name != symbol.qualified_name
                and symbol.qualified_name.startswith(parent.qualified_name + ".")
            ]

            # gopls represents receiver methods as ``pkg.(Type).Method``.
            # Their owner is semantic rather than a lexically enclosing span.
            if ".(" in symbol.qualified_name:
                prefix, receiver_and_method = symbol.qualified_name.split(".(", 1)
                receiver = receiver_and_method.split(").", 1)[0].lstrip("*")
                receiver_qname = f"{prefix}.{receiver}"
                semantic_candidates.extend(parent for parent in symbols if parent.qualified_name == receiver_qname)
            if candidates:
                parent = min(
                    candidates,
                    key=lambda item: (item.end_line - item.start_line, item.qualified_name),
                )
                container_id = canonical_by_qname[parent.qualified_name]
            elif semantic_candidates:
                container_id = canonical_by_qname[
                    max(
                        semantic_candidates,
                        key=lambda item: (len(item.qualified_name), item.qualified_name),
                    ).qualified_name
                ]
            else:
                container_id = file_node_id(str(symbol.file_path.resolve()))
            if container_id in program_graph.nodes:
                program_graph.add_edge(
                    ProgramEdge(
                        ProgramEdgeKind.CONTAINS,
                        container_id,
                        canonical_by_qname[symbol.qualified_name],
                    )
                )

    for child, info in sorted(result.hierarchy.items()):
        canonical_child = canonical_by_qname.get(child)
        if canonical_child is None:
            continue
        for parent in sorted(info.get("superclasses", [])):
            canonical_parent = canonical_by_qname.get(parent)
            if canonical_parent is not None and canonical_parent != canonical_child:
                program_graph.add_edge(ProgramEdge(ProgramEdgeKind.INHERITS, canonical_child, canonical_parent))

    for dependency in result.imports:
        source_id = file_node_id(str(Path(dependency.source_file).resolve()))
        if source_id not in program_graph.nodes:
            continue
        if dependency.target_file:
            target_id = file_node_id(str(Path(dependency.target_file).resolve()))
            if target_id not in program_graph.nodes:
                continue
        else:
            external_name = dependency.external_package or dependency.declared_module
            target_id = external_package_node_id(language, external_name)
            program_graph.add_node(
                ProgramNode(
                    node_id=target_id,
                    kind=ProgramNodeKind.EXTERNAL_PACKAGE,
                    language=language.lower(),
                    name=external_name,
                    metadata={"declared_module": dependency.declared_module},
                )
            )
        program_graph.add_edge(
            ProgramEdge(
                ProgramEdgeKind.IMPORTS,
                source_id,
                target_id,
                occurrences=[
                    ProgramOccurrence(
                        file=dependency.source_file,
                        line=dependency.line,
                        column=dependency.column,
                    )
                ],
                metadata={
                    "declared_module": dependency.declared_module,
                    "declared_modules": [dependency.declared_module],
                },
            )
        )

    return {
        "program_graph": program_graph,
        "source_files": result.source_files,
        "diagnostics": {},
    }


def _map_symbol_kind(kind: int) -> NodeType:
    """Map an LSP SymbolKind integer to CodeBoarding's NodeType.

    Since NodeType uses the same integer values as LSP SymbolKind,
    this is a direct conversion with a fallback to FUNCTION.
    """
    try:
        return NodeType(kind)
    except ValueError:
        return NodeType.FUNCTION
