"""Build file indexes and refresh method spans from live control-flow graphs."""

from collections.abc import Iterable
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from agents.content_hash import (
    MethodRef,
    MethodSpan,
    SourceCache,
    hash_method_body,
    hash_whole_file,
    read_source_lines,
)
from agents.file_index_models import FileEntry, MethodEntry
from repo_utils.change_detector import ChangeSet
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults


def build_files_index(
    analysis: AnalysisInsights,
    repo_dir: Path,
    source_cache: SourceCache | None = None,
) -> dict[str, FileEntry]:
    """Build the file index and hash each method at its current span."""
    file_cache = source_cache if source_cache is not None else {}
    files: dict[str, FileEntry] = {}
    for component in analysis.components:
        for file_methods in component.file_methods:
            entry = files.setdefault(file_methods.file_path, FileEntry())
            source_lines = read_source_lines(repo_dir, file_methods.file_path, file_cache)
            indexed_methods: list[MethodEntry] = []
            for method in file_methods.methods:
                indexed_method = method.model_copy(deep=True)
                indexed_method.content_hash = hash_method_body(
                    source_lines,
                    method.start_line,
                    method.end_line,
                )
                indexed_methods.append(indexed_method)

            entry.merge_from(
                FileEntry(
                    methods=indexed_methods,
                    content_hash=hash_whole_file(source_lines),
                )
            )
    return files


def refresh_method_spans_from_cfg(
    analysis: AnalysisInsights,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
) -> None:
    """Refresh persisted method spans from the live CFG."""
    spans = _cfg_method_spans(static_analysis, repo_dir)
    for component in analysis.components:
        for file_methods in component.file_methods:
            for method in file_methods.methods:
                span = spans.get(MethodRef(file_methods.file_path, method.qualified_name))
                if span is None:
                    method.start_line, method.end_line = 0, 0
                else:
                    method.start_line, method.end_line = span


def _cfg_method_spans(
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
) -> dict[MethodRef, MethodSpan]:
    spans: dict[MethodRef, MethodSpan] = {}
    for language in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_program_graph(language)
        except (KeyError, ValueError):
            continue
        for qualified_name, node in cfg.symbols.items():
            file_path = normalize_repo_path(node.file_path, repo_dir)
            spans.setdefault(MethodRef(file_path, qualified_name), MethodSpan(node.line_start, node.line_end))
    return spans


def changed_member_qnames(
    analyses: Iterable[AnalysisInsights],
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
    changes: ChangeSet | None = None,
) -> set[str]:
    """Canonical qnames whose method body changed since the baseline.

    Compares each tracked method's stored ``content_hash`` against a fresh hash at
    its live CFG span. Independent of the graph fingerprint, so a body-only edit
    that leaves cluster membership untouched still surfaces. When *changes* is
    given, only files the fingerprint flagged are considered, so span drift in an
    untouched file cannot masquerade as an edit. Qnames resolve to the graph's
    canonical id — the same key the cluster member sets use — so an alias in the
    persisted index still joins the cluster it belongs to.
    """
    canonical = _canonical_by_qname(static_analysis)
    changed_files = _changed_file_paths(changes, repo_dir) if changes is not None else None
    spans = _cfg_method_spans(static_analysis, repo_dir)
    source_cache: SourceCache = {}

    changed: set[str] = set()
    for analysis in analyses:
        for component in analysis.components:
            for group in component.file_methods:
                rel = normalize_repo_path(group.file_path, repo_dir)
                if changed_files is not None and rel not in changed_files:
                    continue
                lines = read_source_lines(repo_dir, rel, source_cache)
                for method in group.methods:
                    qname = canonical.get(method.qualified_name, method.qualified_name)
                    span = spans.get(MethodRef(rel, qname))
                    if span is None:
                        continue
                    fresh_hash = hash_method_body(lines, span.start_line, span.end_line)
                    if not fresh_hash or not method.content_hash:
                        continue
                    if fresh_hash != method.content_hash:
                        changed.add(qname)
    return changed


def _canonical_by_qname(static_analysis: StaticAnalysisResults) -> dict[str, str]:
    """Map each symbol alias to its canonical graph node id (the cluster-member key)."""
    mapping: dict[str, str] = {}
    for language in static_analysis.get_languages():
        try:
            graph = static_analysis.get_program_graph(language)
        except (KeyError, ValueError):
            continue
        for node in graph.nodes.values():
            for alias in node.metadata.get("aliases", []):
                mapping[alias] = node.node_id
    return mapping


def _changed_file_paths(changes: ChangeSet, repo_dir: Path) -> set[str]:
    """Repo-relative paths touched by *changes*; renames contribute old and new paths."""
    paths: set[str] = set()
    for file_change in changes.files:
        paths.add(normalize_repo_path(file_change.file_path, repo_dir))
        if file_change.old_path:
            paths.add(normalize_repo_path(file_change.old_path, repo_dir))
    return paths
