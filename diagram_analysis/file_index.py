"""Build file indexes and refresh method spans from live control-flow graphs."""

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
from agents.file_index_models import FileEntry
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
            entry = files.get(file_methods.file_path)
            if entry is None:
                entry = FileEntry(
                    methods=[],
                    content_hash=hash_whole_file(read_source_lines(repo_dir, file_methods.file_path, file_cache)),
                )
                files[file_methods.file_path] = entry

            methods_by_qname = {method.qualified_name: method for method in entry.methods}
            for method in file_methods.methods:
                if method.qualified_name in methods_by_qname:
                    continue
                indexed_method = method.model_copy(deep=True)
                indexed_method.content_hash = hash_method_body(
                    read_source_lines(repo_dir, file_methods.file_path, file_cache),
                    method.start_line,
                    method.end_line,
                )
                methods_by_qname[method.qualified_name] = indexed_method

            entry.methods = sorted(
                methods_by_qname.values(),
                key=lambda method: (method.start_line, method.end_line, method.qualified_name),
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
            cfg = static_analysis.get_cfg(language)
        except (KeyError, ValueError):
            continue
        for qualified_name, node in cfg.nodes.items():
            file_path = normalize_repo_path(node.file_path, repo_dir)
            spans.setdefault(MethodRef(file_path, qualified_name), MethodSpan(node.line_start, node.line_end))
    return spans
