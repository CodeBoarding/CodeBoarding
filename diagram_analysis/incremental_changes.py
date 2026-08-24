"""Compute member-level content changes for incremental analysis."""

from dataclasses import dataclass, field
from pathlib import Path

from agents.content_hash import (
    MethodSpan,
    SourceCache,
    hash_file_residual,
    hash_method_body,
    hash_whole_file,
    read_source_lines,
)
from agents.file_index_models import FileEntry
from repo_utils.change_detector import ChangeSet
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults


@dataclass
class ChangedMembers:
    """Member-granular content changes and unattributed changed files."""

    members: set[str] = field(default_factory=set)
    unattributed_files: set[str] = field(default_factory=set)


def compute_changed_members(
    baseline_files: dict[str, FileEntry],
    new_static: StaticAnalysisResults,
    changes: ChangeSet,
    repo_dir: Path,
    source_cache: SourceCache | None = None,
) -> ChangedMembers:
    """Diff persisted and live method bodies inside changed files."""
    file_cache: SourceCache = source_cache if source_cache is not None else {}
    changed_paths = {normalize_repo_path(fc.file_path, repo_dir) for fc in changes.files if fc.is_content_change()}
    if not changed_paths:
        return ChangedMembers()

    new_member_hashes, new_member_spans = _live_member_hashes(new_static, repo_dir, changed_paths, file_cache)

    result = ChangedMembers()
    for path in changed_paths:
        baseline_entry = baseline_files.get(path)
        baseline_members = (
            {method.qualified_name: method.content_hash for method in baseline_entry.methods}
            if baseline_entry is not None
            else {}
        )
        new_members = new_member_hashes.get(path, {})

        file_changed: set[str] = set()
        for qname in set(baseline_members) | set(new_members):
            in_baseline = qname in baseline_members
            in_new = qname in new_members
            if in_baseline and in_new:
                if baseline_members[qname] != new_members[qname]:
                    file_changed.add(qname)
            else:
                file_changed.add(qname)

        result.members |= file_changed
        baseline_module_hash = baseline_entry.module_hash if baseline_entry is not None else ""
        new_module_hash = hash_file_residual(
            read_source_lines(repo_dir, path, file_cache), new_member_spans.get(path, [])
        )
        module_changed = new_module_hash != baseline_module_hash

        if file_changed:
            if module_changed and baseline_module_hash:
                result.unattributed_files.add(path)
            continue

        baseline_file_hash = baseline_entry.content_hash if baseline_entry is not None else ""
        new_file_hash = hash_whole_file(read_source_lines(repo_dir, path, file_cache))
        if new_file_hash != baseline_file_hash:
            result.unattributed_files.add(path)
    return result


def _live_member_hashes(
    new_static: StaticAnalysisResults,
    repo_dir: Path,
    changed_paths: set[str],
    file_cache: SourceCache,
) -> tuple[dict[str, dict[str, str]], dict[str, list[MethodSpan]]]:
    """Build live method hashes and spans for changed files."""
    hashes: dict[str, dict[str, str]] = {}
    spans: dict[str, list[MethodSpan]] = {}
    for language in new_static.get_languages():
        try:
            cfg = new_static.get_cfg(language)
        except (KeyError, ValueError):
            continue
        for qname, node in cfg.nodes.items():
            path = normalize_repo_path(node.file_path, repo_dir)
            if path not in changed_paths or node.is_data():
                continue
            source_lines = read_source_lines(repo_dir, path, file_cache)
            hashes.setdefault(path, {})[qname] = hash_method_body(source_lines, node.line_start, node.line_end)
            spans.setdefault(path, []).append(MethodSpan(node.line_start, node.line_end))
    return hashes, spans
