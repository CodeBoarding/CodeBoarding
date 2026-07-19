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
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
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


def refresh_method_locations_from_cfg(
    analysis: AnalysisInsights,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
) -> None:
    """Move retained method and key-entity references to their live locations."""
    locations: dict[str, list[tuple[str, int, int]]] = {}
    for language in static_analysis.get_languages():
        try:
            graph = static_analysis.get_program_graph(language)
        except (KeyError, ValueError):
            continue
        for node in graph.symbol_nodes():
            location = (normalize_repo_path(node.file_path, repo_dir), node.line_start, node.line_end)
            if location not in locations.setdefault(node.id, []):
                locations[node.id].append(location)
    for candidates in locations.values():
        candidates.sort()

    for component in analysis.components:
        relocated: dict[str, list[MethodEntry]] = {}
        for group in component.file_methods:
            for method in group.methods:
                location = _resolve_live_location(locations.get(method.qualified_name, []), group.file_path)
                file_path = group.file_path
                if location is not None:
                    file_path, method.start_line, method.end_line = location
                relocated.setdefault(file_path, []).append(method)
        component.file_methods = [
            FileMethodGroup(
                file_path=file_path,
                methods=sorted(
                    methods,
                    key=lambda method: (method.start_line, method.end_line, method.qualified_name),
                ),
            )
            for file_path, methods in relocated.items()
        ]

        for reference in component.key_entities:
            location = _resolve_live_location(
                locations.get(reference.qualified_name, []),
                reference.reference_file or "",
            )
            if location is None:
                continue
            reference.reference_file, reference.reference_start_line, reference.reference_end_line = location


def _resolve_live_location(
    candidates: list[tuple[str, int, int]],
    current_file: str,
) -> tuple[str, int, int] | None:
    same_file = [location for location in candidates if location[0] == current_file]
    if len(same_file) == 1:
        return same_file[0]
    return candidates[0] if len(candidates) == 1 else None


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
