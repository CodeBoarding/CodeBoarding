"""Bridge between engine models and CodeBoarding's CallGraph/Node/Edge models."""

from __future__ import annotations

import logging

from collections import Counter

from static_analyzer.constants import CLASS_TYPES, GRAPH_NODE_TYPES, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import LanguageAnalysisResult
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.graph import CallGraph, EdgeKind
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
                if edge.call_sites:
                    call_graph.add_edge(
                        src,
                        dst,
                        call_sites=[
                            {"file": site.file, "line": site.line, "column": site.column} for site in edge.call_sites
                        ],
                    )
                else:
                    logger.warning(
                        "edge_with_no_site language=%s source=%s destination=%s",
                        language,
                        src,
                        dst,
                    )
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

    _add_reference_edges(call_graph, result)

    logger.info(
        "Reference edges for %s: %d (%s)",
        language,
        len(call_graph.reference_edges),
        dict(_count_by_kind(call_graph.reference_edges)),
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


def _add_reference_edges(call_graph: CallGraph, result: LanguageAnalysisResult) -> None:
    """Complete the graph with non-call relationship edges (see ``EdgeKind``).

    CONTAINS and INHERITS need no extra LSP work — they come from the qualified-name
    hierarchy and the already-computed class hierarchy. TYPEREF and IMPORT are read
    from the engine result when the analyzer populated them.
    """
    class_qnames = {qname for qname, node in call_graph.nodes.items() if node.type in CLASS_TYPES}

    # CONTAINS: each method / nested symbol -> its innermost enclosing class node.
    for qname in call_graph.nodes:
        if qname in class_qnames:
            continue
        parts = qname.split(".")
        for i in range(len(parts) - 1, 0, -1):
            parent = ".".join(parts[:i])
            if parent in class_qnames:
                call_graph.add_reference_edge(qname, parent, EdgeKind.CONTAINS)
                break

    # INHERITS: child class -> each superclass (already computed by HierarchyBuilder).
    for child, info in (result.hierarchy or {}).items():
        for superclass in info.get("superclasses", []):
            call_graph.add_reference_edge(child, superclass, EdgeKind.INHERITS)

    # TYPEREF / IMPORT: emitted by the analyzer when available (see engine models).
    for src, dst in getattr(result, "type_references", None) or ():
        call_graph.add_reference_edge(src, dst, EdgeKind.TYPEREF)
    for src, dst in getattr(result, "import_edges", None) or ():
        call_graph.add_reference_edge(src, dst, EdgeKind.IMPORT)


def _count_by_kind(reference_edges: list[tuple[str, str, str]]) -> Counter:
    return Counter(kind for _, _, kind in reference_edges)


def _map_symbol_kind(kind: int) -> NodeType:
    """Map an LSP SymbolKind integer to CodeBoarding's NodeType.

    Since NodeType uses the same integer values as LSP SymbolKind,
    this is a direct conversion with a fallback to FUNCTION.
    """
    try:
        return NodeType(kind)
    except ValueError:
        return NodeType.FUNCTION
