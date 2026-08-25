"""Build file indexes and refresh method spans from live control-flow graphs."""

from collections import defaultdict
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from agents.content_hash import (
    MethodRef,
    MethodSpan,
    SourceCache,
    hash_file_residual,
    hash_method_body,
    hash_whole_file,
    read_source_lines,
)
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from repo_utils.ignore import RepoIgnoreManager
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.config import CALLABLE_TYPES, CLASS_TYPES
from static_analyzer.node import Node


def build_file_methods_from_nodes(
    nodes: list[Node],
    repo_dir: Path,
    source_cache: SourceCache | None = None,
) -> list[FileMethodGroup]:
    """Build sorted file/method groups from assigned CFG nodes."""
    by_file: dict[str, dict[tuple[int, int, str, str], MethodEntry]] = defaultdict(dict)
    file_cache = source_cache if source_cache is not None else {}

    for node in nodes:
        if node.type not in CALLABLE_TYPES | CLASS_TYPES:
            continue
        file_path = normalize_repo_path(node.file_path, repo_dir)
        method_name = node.fully_qualified_name.split(".")[-1]
        dedupe_key = (node.line_start, node.line_end, node.type.name, method_name)
        candidate = MethodEntry(
            qualified_name=node.fully_qualified_name,
            start_line=node.line_start,
            end_line=node.line_end,
            node_type=node.type.name,
            content_hash=hash_method_body(
                read_source_lines(repo_dir, file_path, file_cache),
                node.line_start,
                node.line_end,
            ),
        )
        existing = by_file[file_path].get(dedupe_key)
        if existing is None or _is_more_specific(candidate.qualified_name, existing.qualified_name):
            by_file[file_path][dedupe_key] = candidate

    return [
        FileMethodGroup(
            file_path=file_path,
            methods=sorted(
                methods.values(), key=lambda method: (method.start_line, method.end_line, method.qualified_name)
            ),
        )
        for file_path, methods in sorted(by_file.items())
    ]


def build_files_index(
    analysis: AnalysisInsights,
    repo_dir: Path,
    source_cache: SourceCache | None = None,
) -> dict[str, FileEntry]:
    """Build the file index and hash each method at its current span.

    Ignored files are skipped here, not only stripped from components later: the file
    index and the methods_index derived from it are built before ``_strip_ignored`` runs,
    so without this filter an ignored file (a test module) would still appear in the saved
    ``files``/``methods_index`` even though no component owns it.
    """
    file_cache = source_cache if source_cache is not None else {}
    ignore_manager = RepoIgnoreManager(repo_dir)
    files: dict[str, FileEntry] = {}
    for component in analysis.components:
        for file_methods in component.file_methods:
            if ignore_manager.should_ignore(Path(file_methods.file_path)):
                continue
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
    # module_hash excises *all* of a file's method spans, so it must be computed once the
    # file's methods are gathered across every component — a file split across components
    # would otherwise count a sibling component's methods as module-level.
    for file_path, entry in files.items():
        source_lines = read_source_lines(repo_dir, file_path, file_cache)
        spans = [MethodSpan(method.start_line, method.end_line) for method in entry.methods]
        entry.module_hash = hash_file_residual(source_lines, spans)
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
            cfg = static_analysis.get_cfg(language)
        except (KeyError, ValueError):
            continue
        for qualified_name, node in cfg.nodes.items():
            file_path = normalize_repo_path(node.file_path, repo_dir)
            spans.setdefault(MethodRef(file_path, qualified_name), MethodSpan(node.line_start, node.line_end))
    return spans


def _is_more_specific(candidate: str, current: str) -> bool:
    candidate_parts = candidate.split(".")
    current_parts = current.split(".")
    if candidate_parts[-1] == current_parts[-1]:
        return len(candidate_parts) > len(current_parts)
    return len(candidate) > len(current)
