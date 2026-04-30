"""Resolve file paths to current-on-disk method entries.

Why from CFG nodes (not ``iter_reference_nodes``): the incremental
updater needs the *post-change* view of each file to diff against the
*pre-change* view stored on ``AnalysisInsights.files``.
``prepare_static_analysis()`` populates ``static_analysis`` from a fresh
worktree scan immediately before this resolver is built — that is the
"current" side of the diff. Routing both sides through the same
canonical projection (``agents.method_projection.build_file_method_groups``)
is what keeps the diff honest; otherwise alias qualified names and
anonymous-callback filtering drift between sides and fabricate
add/delete pairs that block ``_is_pure_in_place_edit``.
"""

from pathlib import Path

from agents.agent_responses import MethodEntry
from agents.method_projection import build_file_method_groups
from static_analyzer.analysis_result import StaticAnalysisResults


def collect_method_entries(static_analysis: StaticAnalysisResults, repo_dir: Path) -> dict[str, list[MethodEntry]]:
    """Return ``{relative_file_path: [MethodEntry, ...]}`` from CFG nodes.

    Iterates every language's ``CallGraph`` and feeds the union through
    the canonical projection helper. Identical projection logic to the
    full-analysis path means a comments-only edit produces matching qname
    sets on both sides — no spurious added/deleted methods.
    """
    nodes = []
    for language in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_cfg(language)
        except ValueError:
            # Some languages have source files registered but no CFG yet
            # (parser bootstrap failure, language disabled). Skip; the
            # full-analysis path makes the same accommodation.
            continue
        nodes.extend(cfg.nodes.values())

    methods_by_file: dict[str, list[MethodEntry]] = {}
    for group in build_file_method_groups(nodes, repo_dir):
        methods_by_file[group.file_path] = list(group.methods)
    return methods_by_file


class StaticAnalysisSymbolResolver:
    """Resolve file paths to their current ``MethodEntry`` list via static analysis."""

    def __init__(self, static_analysis: StaticAnalysisResults, repo_dir: Path) -> None:
        self._repo_dir = repo_dir
        self._methods_by_file = collect_method_entries(static_analysis, repo_dir)

    def __call__(self, file_path: str) -> list[MethodEntry]:
        return self.resolve(file_path)

    def resolve(self, file_path: str) -> list[MethodEntry]:
        # ``build_file_method_groups`` produces repo-relative POSIX-ish keys
        # via os.path.relpath. Normalize the lookup key the same way so we
        # match callers that pass either absolute or already-relative paths.
        if Path(file_path).is_absolute():
            try:
                normalized = str(Path(file_path).relative_to(self._repo_dir))
            except ValueError:
                normalized = file_path
        else:
            normalized = file_path
        return self._methods_by_file.get(normalized, [])
