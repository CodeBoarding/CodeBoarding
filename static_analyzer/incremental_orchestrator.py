"""Refresh affected edges against the same lossless symbols used by full analysis."""

from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_cache import invalidate_files, merge_results
from static_analyzer.cfg import CallGraph, EdgeKind
from static_analyzer.engine.analysis_context import AnalysisContext
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import EdgeStrategy
from static_analyzer.engine.models import LanguageAnalysisResult
from static_analyzer.engine.result_converter import convert_to_codeboarding_format


class MissingSymbolSnapshotError(RuntimeError):
    """The cached graph cannot substitute for a lossless declaration snapshot."""


def affected_source_files(
    cached_analysis: dict,
    changed_files: set[Path],
    adapter: LanguageAdapter,
    context: AnalysisContext,
) -> set[Path]:
    """Select callers affected by edits, unresolved calls, and dispatch dependencies."""
    if not changed_files:
        return set()
    affected = {path for path in changed_files if path.is_file()}
    affected.update(Path(path) for path in cached_analysis.get("unresolved_files", set()))
    target_files = set(changed_files)
    graph = cached_analysis["call_graph"]
    if adapter.expands_virtual_dispatch:
        table = context.symbols_for(adapter)
        # This is a conservative file dependency closure, not symbol resolution.
        ancestors = set(changed_files)
        pending = list(changed_files)
        while pending:
            file_path = pending.pop()
            bases = (
                {base for _, names in context.source_inspector.find_type_bases(file_path) for base in names}
                if file_path.is_file()
                else set()
            )
            dependencies = {
                sym.file_path
                for sym in table.symbols.values()
                if adapter.is_class_like(sym.kind) and (sym.name in bases or sym.qualified_name in bases)
            }
            dependencies.update(
                Path(graph.nodes[ref.dst].file_path)
                for ref in graph.reference_edges
                if ref.kind is EdgeKind.INHERITS
                and ref.src in graph.nodes
                and ref.dst in graph.nodes
                and Path(graph.nodes[ref.src].file_path) == file_path
            )
            pending.extend(dependencies - ancestors)
            ancestors.update(dependencies)
        target_files.update(ancestors)
    for edge in graph.edges:
        if Path(edge.dst_node.file_path) in target_files:
            affected.add(Path(edge.src_node.file_path))
            affected.update(Path(str(site["file"])) for site in edge.call_sites)
    return {path for path in affected if path.is_file() and path.suffix in adapter.file_extensions}


def update_cfg_for_changed_files(
    cached_analysis: dict,
    changed_files: set[Path],
    adapter: LanguageAdapter,
    project_path: Path,
    engine_client: LSPClient,
    ignore_manager: RepoIgnoreManager,
    context: AnalysisContext | None = None,
    query_files: set[Path] | None = None,
) -> dict:
    """Requery affected files without rediscovering an injected, frozen context."""
    if context is None and cached_analysis.get("symbols") is None:
        raise MissingSymbolSnapshotError(
            "Incremental analysis requires a cached symbol snapshot. Run a full analysis first to refresh the cache."
        )
    if not changed_files and not query_files:
        return cached_analysis

    root = project_path.resolve()

    def eligible(path: Path) -> bool:
        return (
            path.is_file()
            and path.resolve().is_relative_to(root)
            and path.suffix in adapter.file_extensions
            and not ignore_manager.should_ignore(path)
        )

    standalone = context is None
    if context is None:
        context = AnalysisContext()
        context.hydrate(
            adapter,
            cached_analysis["symbols"],
            cached_analysis.get("unresolved_files", set()),
            cached_analysis.get("closed_documents", set()),
        )
    table = context.symbols_for(adapter)
    builder = CallGraphBuilder(engine_client, adapter, project_path, context=context)
    changed_sources = sorted(path for path in changed_files if eligible(path))
    if standalone:
        engine_client.refresh_files(changed_files, {Path(p) for p in cached_analysis["source_files"]})
        table.remove_files(changed_files)
        builder.collect_symbols(changed_sources)
        context.freeze()
    # An injected context may have no changed declarations in this particular engine.
    context.prepared.setdefault((adapter.results_language, root), changed_sources)
    queries = {
        path
        for path in (
            affected_source_files(cached_analysis, changed_files, adapter, context)
            if query_files is None
            else query_files
        )
        if eligible(path)
    }
    kept = invalidate_files(cached_analysis, changed_files).analysis
    graph = CallGraph(language=adapter.language)
    for node in kept.call_graph.nodes.values():
        graph.add_node(node)
    for edge in kept.call_graph.edges:
        if Path(edge.src_node.file_path) in queries:
            continue
        sites = [site for site in edge.call_sites if Path(str(site["file"])) not in queries]
        if edge.call_sites and not sites:
            continue
        graph.add_edge(edge.get_source(), edge.get_destination(), call_sites=sites)
    for ref in kept.call_graph.reference_edges:
        graph.add_reference_edge(ref)
    kept.call_graph = graph
    kept.class_hierarchies = {name: info for name, info in kept.class_hierarchies.items() if name in graph.nodes}

    context.unresolved_files.difference_update(str(path) for path in queries)
    if standalone:
        context.unresolved_files.difference_update(str(path) for path in changed_files)
    reference_files = set(queries)
    if adapter.edge_strategy != EdgeStrategy.DEFINITIONS:
        # Revalidate non-invocation references from changed callers as well.
        reference_files.update(
            Path(edge.dst_node.file_path)
            for edge in cached_analysis["call_graph"].edges
            if (
                Path(edge.src_node.file_path) in queries
                or any(Path(str(site["file"])) in queries for site in edge.call_sites)
            )
            and eligible(Path(edge.dst_node.file_path))
        )
    engine_result = (
        builder.build(sorted(reference_files), definition_files=tuple(sorted(queries)))
        if reference_files
        else LanguageAnalysisResult()
    )
    fresh = convert_to_codeboarding_format(table, engine_result, adapter, ignore_manager)
    fresh["source_files"] = sorted(
        {Path(path) for path in table.file_symbols} | {Path(path) for path in kept.source_files} | set(changed_sources)
    )
    fresh["symbols"] = table.snapshot()
    fresh["unresolved_files"] = context.unresolved_files & {str(path) for path in fresh["source_files"]}
    fresh["closed_documents"] = {str(p) for p in context.closed_documents if str(p) in table.file_symbols}
    fresh["diagnostics"] = {**(kept.diagnostics or {}), **engine_client.get_collected_diagnostics()}
    return merge_results(kept, fresh).to_dict()
