"""Bridge between engine models and CodeBoarding's CallGraph/Node/Edge models."""

from __future__ import annotations

import logging

from static_analyzer.constants import GRAPH_NODE_TYPES, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import LanguageAnalysisResult
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


def convert_to_codeboarding_format(
    symbol_table: SymbolTable,
    result: LanguageAnalysisResult,
    adapter: LanguageAdapter,
) -> dict:
    """Convert engine analysis results to the dict shape expected by StaticAnalyzer.analyze().

    Returns a dict with keys:
        - call_graph: CallGraph (CodeBoarding's graph.py model)
        - class_hierarchies: dict
        - package_relations: dict
        - references: list[Node]
        - source_files: list[str]
        - diagnostics: dict (empty — diagnostics are collected separately)
    """
    language = adapter.language
    call_graph = CallGraph(language=language)

    # Collect all symbol names that participate in edges so we include them as nodes
    edge_participants: set[str] = set()
    for edge in result.cfg.edges:
        edge_participants.add(edge.source)
        edge_participants.add(edge.destination)

    # Build Node objects from the engine's symbol table
    symbol_nodes: dict[str, Node] = {}
    for qname, sym in symbol_table.symbols.items():
        node_type = _map_symbol_kind(sym.kind)
        # Include symbols that are graph node types OR that participate in edges
        if node_type not in GRAPH_NODE_TYPES and qname not in edge_participants:
            continue

        node = Node(
            fully_qualified_name=qname,
            node_type=node_type,
            file_path=str(sym.file_path),
            line_start=sym.start_line + 1,
            line_end=sym.end_line + 1,
            col_start=sym.start_char,
        )
        symbol_nodes[qname] = node
        call_graph.add_node(node)

    # Add edges from the engine's CFG
    edges_added = 0
    edges_skipped = 0
    for edge in result.cfg.edges:
        src = edge.source
        dst = edge.destination
        if call_graph.has_node(src) and call_graph.has_node(dst):
            try:
                call_graph.add_edge(src, dst)
                edges_added += 1
            except ValueError:
                edges_skipped += 1
        else:
            edges_skipped += 1

    logger.info(
        "Converted %d nodes, %d edges (%d skipped) for %s",
        len(call_graph.nodes),
        edges_added,
        edges_skipped,
        language,
    )

    # Build references list from primary symbols only (excludes dual-registration
    # aliases and local variables/parameters that are implementation noise).
    primary_qnames: set[str] = set()
    for syms in symbol_table.primary_file_symbols.values():
        for sym in syms:
            primary_qnames.add(sym.qualified_name)

    references: list[Node] = []
    seen_refs: set[str] = set()
    for qname in sorted(primary_qnames):
        sym = symbol_table.symbols[qname]
        if not adapter.is_reference_worthy(sym.kind):
            continue
        if symbol_table.is_local_variable(sym):
            continue
        if qname in seen_refs:
            continue
        seen_refs.add(qname)

        # Reuse existing node if in the graph, otherwise create a new one
        if qname in symbol_nodes:
            references.append(symbol_nodes[qname])
        else:
            ref_node = Node(
                fully_qualified_name=qname,
                node_type=_map_symbol_kind(sym.kind),
                file_path=str(sym.file_path),
                line_start=sym.start_line + 1,
                line_end=sym.end_line + 1,
            )
            references.append(ref_node)

    return {
        "call_graph": call_graph,
        "class_hierarchies": result.hierarchy,
        "package_relations": result.package_dependencies,
        "references": references,
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
