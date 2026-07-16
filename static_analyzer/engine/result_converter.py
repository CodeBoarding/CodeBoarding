"""Bridge between engine models and CodeBoarding's CallGraph/Node/Edge models."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from static_analyzer.constants import GRAPH_NODE_TYPES, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import LanguageAnalysisResult
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node
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


def convert_to_codeboarding_format(
    symbol_table: SymbolTable,
    result: LanguageAnalysisResult,
    adapter: LanguageAdapter,
    project_root: Path | None = None,
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
    if project_root is None:
        source_paths = [Path(path).resolve() for path in result.source_files]
        project_root = Path(os.path.commonpath([str(path) for path in source_paths])) if source_paths else Path.cwd()
        if project_root.is_file():
            project_root = project_root.parent
    project_root = project_root.resolve()
    program_graph = ProgramGraph(language=language.lower())

    # Collect all symbol names that participate in edges so we include them as nodes
    edge_participants: set[str] = set()
    for edge in result.cfg.edges:
        edge_participants.add(edge.source)
        edge_participants.add(edge.destination)
    primary_qnames = {item.qualified_name for items in symbol_table.primary_file_symbols.values() for item in items}

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

    # CallGraph performs location-based alias canonicalization while nodes are
    # added. Mirror only those canonical symbol identities into ProgramGraph.
    for qname in sorted(call_graph.nodes):
        sym = symbol_table.symbols.get(qname)
        if sym is None:
            continue
        program_graph.add_node(
            ProgramNode(
                node_id=qname,
                kind=ProgramNodeKind.SYMBOL,
                language=language.lower(),
                name=sym.name,
                file_path=str(sym.file_path),
                symbol_type=_map_symbol_kind(sym.kind),
                line_start=sym.start_line + 1,
                line_end=sym.end_line + 1,
                col_start=sym.start_char,
                reference_worthy=(
                    qname in primary_qnames
                    and adapter.is_reference_worthy(sym.kind)
                    and not symbol_table.is_local_variable(sym)
                ),
            )
        )

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
                program_graph.add_edge(
                    ProgramEdge(
                        kind=ProgramEdgeKind.CALL,
                        source=call_graph._resolve_name(src),
                        target=call_graph._resolve_name(dst),
                        occurrences=[
                            ProgramOccurrence(file=site.file, line=site.line, column=site.column)
                            for site in edge.call_sites
                        ],
                    )
                )
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
        if qname not in program_graph.nodes:
            program_graph.add_node(
                ProgramNode(
                    node_id=qname,
                    kind=ProgramNodeKind.SYMBOL,
                    language=language.lower(),
                    name=sym.name,
                    file_path=str(sym.file_path),
                    symbol_type=_map_symbol_kind(sym.kind),
                    line_start=sym.start_line + 1,
                    line_end=sym.end_line + 1,
                    col_start=sym.start_char,
                    reference_worthy=True,
                )
            )

    # Add file/package anchors and their containment hierarchy.
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

    graph_symbol_ids = {node.node_id for node in program_graph.symbol_nodes() if node.node_id in symbol_table.symbols}
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
                container_id = parent.qualified_name
            elif semantic_candidates:
                container_id = max(
                    semantic_candidates,
                    key=lambda item: (len(item.qualified_name), item.qualified_name),
                ).qualified_name
            else:
                container_id = file_node_id(str(symbol.file_path.resolve()))
            if container_id in program_graph.nodes:
                program_graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, container_id, symbol.qualified_name))

    for child, info in sorted(result.hierarchy.items()):
        if child not in program_graph.nodes:
            continue
        for parent in sorted(info.get("superclasses", [])):
            if parent in program_graph.nodes:
                program_graph.add_edge(ProgramEdge(ProgramEdgeKind.INHERITS, child, parent))

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
        "call_graph": call_graph,
        "class_hierarchies": result.hierarchy,
        "package_relations": result.package_dependencies,
        "references": references,
        "source_files": result.source_files,
        "diagnostics": {},
        "program_graph": program_graph,
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
