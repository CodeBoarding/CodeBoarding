"""Pkl warm-start updater: bring a cached per-language analysis up to date in-memory.

Given a cached analysis dict (loaded from the SHA-tagged pkl) and the file
list git reports as changed since the pkl's tag SHA, rerun the LSP for those
files only and merge the result with the kept-from-cache state. No JSON, no
disk writes — the only persistence is the pkl save that ``StaticAnalyzer``
performs after this returns.
"""

import logging
from pathlib import Path
from typing import Any

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_cache import invalidate_files, merge_results
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


def update_cfg_for_changed_files(
    cached_analysis: dict[str, Any],
    changed_files: set[Path],
    adapter: LanguageAdapter,
    project_path: Path,
    engine_client: LSPClient,
    ignore_manager: RepoIgnoreManager,
) -> dict[str, Any]:
    """Apply *changed_files* to *cached_analysis* via re-LSP-and-merge.

    Steps:

    1. ``invalidate_files`` drops every node/edge/reference/class/package
       entry sourced from a changed file, leaving the cached state of every
       *unchanged* file intact.
    2. The LSP re-analyses just the changed files (existing ones; deleted
       files contribute nothing).
    3. ``merge_results`` unions the kept-from-cache state with the fresh
       per-file result.
    4. Surviving entries are filtered against the live filesystem so a
       deleted file's references / classes / package members are removed
       from the merged dict.

    Returns a fresh dict with the same shape as ``cached_analysis``. The
    caller stuffs it into ``StaticAnalysisResults`` and saves the pkl
    tagged with the *current* source SHA.
    """
    if not changed_files:
        return cached_analysis

    existing_files = {f for f in changed_files if f.exists()}
    deleted_files = {f for f in changed_files if not f.exists()}
    logger.info(
        "update_cfg_for_changed_files: %d changed (%d existing, %d deleted)",
        len(changed_files),
        len(existing_files),
        len(deleted_files),
    )

    updated_cache = invalidate_files(cached_analysis, changed_files)

    changed_source_files = [
        f for f in existing_files if f.suffix in adapter.file_extensions and not ignore_manager.should_ignore(f)
    ]

    if changed_source_files:
        builder = CallGraphBuilder(engine_client, adapter, project_path)
        engine_result = builder.build(changed_source_files)
        new_analysis = convert_to_codeboarding_format(builder.symbol_table, engine_result, adapter)
    else:
        new_analysis = {
            "call_graph": CallGraph(language=adapter.language),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
            "diagnostics": {},
        }

    fresh_diagnostics = engine_client.get_collected_diagnostics()
    if fresh_diagnostics:
        new_analysis["diagnostics"] = fresh_diagnostics

    merged_analysis = merge_results(updated_cache, new_analysis)
    return _filter_to_live_files(merged_analysis)


def _filter_to_live_files(merged_analysis: dict[str, Any]) -> dict[str, Any]:
    """Drop entries whose file no longer exists on disk.

    A file in ``source_files`` may have been re-LSPed earlier in the run and
    then removed by a subsequent edit; this final filter keeps the merged
    dict consistent with the live filesystem.
    """
    all_existing = {f for f in merged_analysis.get("source_files", []) if f.exists()}
    existing_file_strs = {str(f) for f in all_existing}

    merged_analysis["source_files"] = list(all_existing)
    merged_analysis["references"] = [
        ref for ref in merged_analysis.get("references", []) if ref.file_path in existing_file_strs
    ]

    merged_cg = merged_analysis.get("call_graph", CallGraph())
    filtered_cg = CallGraph()
    for node in merged_cg.nodes.values():
        if node.file_path in existing_file_strs:
            filtered_cg.add_node(node)
    for edge in merged_cg.edges:
        src, dst = edge.get_source(), edge.get_destination()
        if filtered_cg.has_node(src) and filtered_cg.has_node(dst):
            try:
                filtered_cg.add_edge(src, dst)
            except ValueError:
                pass
    merged_analysis["call_graph"] = filtered_cg

    merged_analysis["class_hierarchies"] = {
        name: info
        for name, info in merged_analysis.get("class_hierarchies", {}).items()
        if info.get("file_path") in existing_file_strs
    }

    filtered_packages: dict[str, Any] = {}
    for pkg_name, pkg_info in merged_analysis.get("package_relations", {}).items():
        existing_pkg_files = [f for f in pkg_info.get("files", []) if f in existing_file_strs]
        if existing_pkg_files:
            filtered_packages[pkg_name] = {**pkg_info, "files": existing_pkg_files}
    merged_analysis["package_relations"] = filtered_packages

    return merged_analysis
