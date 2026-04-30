"""Resolve file paths to current-on-disk method entries.

Why from ``static_analysis`` and not ``analysis.files``: the incremental
updater needs the *post-change* view of each file to diff against the
*pre-change* view stored on ``AnalysisInsights.files``.
``prepare_static_analysis()`` populates ``static_analysis`` from a fresh
worktree scan immediately before this resolver is built — that is the
"current" side of the diff.
Collapsing both sides to ``analysis.files`` would always report "no
changes" and silently break incremental analysis.
"""

from collections import defaultdict
from pathlib import Path

from agents.agent_responses import MethodEntry
from static_analyzer.analysis_result import StaticAnalysisResults
from utils import to_relative_path


def normalize_repo_path(path: str, repo_dir: Path) -> str:
    return to_relative_path(path.replace("\\", "/"), repo_dir)


def collect_method_entries(static_analysis: StaticAnalysisResults, repo_dir: Path) -> dict[str, list[MethodEntry]]:
    methods_by_file: dict[str, list[MethodEntry]] = defaultdict(list)

    for node in static_analysis.iter_reference_nodes():
        if node.is_callback_or_anonymous():
            continue
        if not (node.is_callable() or node.is_class()):
            continue
        file_path = normalize_repo_path(str(node.file_path), repo_dir)
        methods_by_file[file_path].append(MethodEntry.from_node(node))

    for file_path in methods_by_file:
        methods_by_file[file_path].sort(
            key=lambda method: (method.start_line, method.end_line, method.qualified_name),
        )

    return methods_by_file


class StaticAnalysisSymbolResolver:
    """Resolve file paths to their current ``MethodEntry`` list via static analysis."""

    def __init__(self, static_analysis: StaticAnalysisResults, repo_dir: Path) -> None:
        self._repo_dir = repo_dir
        self._methods_by_file = collect_method_entries(static_analysis, repo_dir)

    def __call__(self, file_path: str) -> list[MethodEntry]:
        return self.resolve(file_path)

    def resolve(self, file_path: str) -> list[MethodEntry]:
        normalized = normalize_repo_path(file_path, self._repo_dir)
        return self._methods_by_file.get(normalized, [])
