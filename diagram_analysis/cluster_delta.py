"""Deterministic cluster delta computation.

Mirrors ``build_all_cluster_results`` for the incremental path: produces a
``ClusterDelta`` describing which clusters carried over, which changed members,
which are entirely new, and which dropped — without requiring any LLM call.

The clustering itself belongs to ``HierarchicalInfomapClusterer``, which
warm-starts from the graph's own snapshot and carries cluster ids across by
overlap. This module only reports what moved.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.cluster_snapshot import ClusterSnapshot, ClusterSnapshotEntry
from diagram_analysis.io_utils import normalize_repo_path
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult
from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer
from static_analyzer.program_graph import ProgramGraph

logger = logging.getLogger(__name__)


@dataclass
class LanguageDelta:
    language: str
    cluster_results: ClusterResult
    new_cluster_ids: set[int] = field(default_factory=set)
    changed_cluster_ids: set[int] = field(default_factory=set)
    dropped_cluster_ids: set[int] = field(default_factory=set)

    @property
    def affected_cluster_ids(self) -> set[int]:
        return self.new_cluster_ids | self.changed_cluster_ids


@dataclass
class ClusterDelta:
    by_language: dict[str, LanguageDelta] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return any(d.affected_cluster_ids or d.dropped_cluster_ids for d in self.by_language.values())

    def cluster_results(self) -> dict[str, ClusterResult]:
        return {lang: d.cluster_results for lang, d in self.by_language.items()}


@dataclass(frozen=True)
class ClusterRef:
    language: str
    cluster_id: int
    scope_id: str = ROOT_SCOPE_ID


@dataclass
class ClusterMemberDelta:
    old_cluster: ClusterRef
    new_cluster: ClusterRef
    unchanged_methods: set[str] = field(default_factory=set)
    added_methods: set[str] = field(default_factory=set)
    removed_methods: set[str] = field(default_factory=set)
    # Members whose body changed (member-granular dirty signal; drives "modified").
    dirty_members: set[str] = field(default_factory=set)
    # Changed files touching this cluster — display context only, never gating.
    dirty_files: set[str] = field(default_factory=set)


@dataclass
class ClusterReshape:
    old_clusters: list[ClusterRef] = field(default_factory=list)
    new_clusters: list[ClusterRef] = field(default_factory=list)
    overlap_counts: dict[tuple[ClusterRef, ClusterRef], int] = field(default_factory=dict)
    dirty_members: set[str] = field(default_factory=set)
    dirty_files: set[str] = field(default_factory=set)


@dataclass
class LanguageStructuralDiff:
    language: str
    unchanged: list[ClusterMemberDelta] = field(default_factory=list)
    modified: list[ClusterMemberDelta] = field(default_factory=list)
    new: list[ClusterRef] = field(default_factory=list)
    new_details: list[ClusterMemberDelta] = field(default_factory=list)
    removed: list[ClusterRef] = field(default_factory=list)
    reshaped: list[ClusterReshape] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.modified or self.new or self.removed or self.reshaped)


@dataclass
class StructuralClusterDiff:
    by_language: dict[str, LanguageStructuralDiff] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return any(diff.has_changes for diff in self.by_language.values())


def compute_cluster_delta(
    old_snapshot: ClusterSnapshot,
    new_static: StaticAnalysisResults,
    changes: ChangeSet | None = None,
    repo_dir: Path | None = None,
    changed_members: set[str] | None = None,
) -> ClusterDelta:
    """Compute per-language cluster deltas from a fresh clustering of each graph.

    Each language's ProgramGraph is clustered by ``HierarchicalInfomapClusterer``,
    which warm-starts from its own snapshot; this diffs the result against
    ``old_snapshot`` into new, changed and dropped cluster ids. ``changes`` and
    ``repo_dir`` are used only to report symbols that moved without their file
    appearing in the diff (``changes=None`` skips that check). ``changed_members``
    (body-edited qnames) is reported alongside for diagnostics.
    """
    delta = ClusterDelta()
    diff_files = _changeset_to_path_set(changes) if changes is not None else None
    members = changed_members if changed_members is not None else set()
    new_languages = {str(language) for language in new_static.get_languages()}
    for language in new_static.get_languages():
        program_graph = new_static.get_program_graph(language)
        old_clusters = old_snapshot.get_language(str(language))
        delta.by_language[str(language)] = _delta_for_language(
            str(language),
            program_graph,
            old_clusters,
            members,
            diff_files,
            repo_dir,
        )
    # A language whose files were all deleted produces no graph; emit its old
    # clusters as dropped so has_changes fires and the components are removed.
    for language in set(old_snapshot.by_language) - new_languages:
        old_clusters = old_snapshot.get_language(language)
        delta.by_language[language] = LanguageDelta(
            language=language,
            cluster_results=ClusterResult(),
            dropped_cluster_ids=set(old_clusters),
        )
    return delta


def structural_diff_from_delta(
    old_snapshot: ClusterSnapshot,
    delta: ClusterDelta,
    changes: ChangeSet | None = None,
    repo_dir: Path | None = None,
    scope_id: str = ROOT_SCOPE_ID,
    changed_members: set[str] | None = None,
) -> StructuralClusterDiff:
    """Classify seeded cluster output into scope-local structural facts.

    A cluster is ``modified`` only when its own members moved (added/removed) or a
    member's body changed (``changed_members``). A file shared with a changed
    cluster is no longer enough — ``changes``/``repo_dir`` populate ``dirty_files``
    for display only.
    """
    diff_files = _changeset_to_path_set(changes) if changes is not None else set()
    members = changed_members if changed_members is not None else set()
    structural = StructuralClusterDiff()
    languages = set(old_snapshot.by_language) | set(delta.by_language)
    for language in sorted(languages):
        old_clusters = old_snapshot.get_language(language)
        language_delta = delta.by_language.get(language)
        new_result = language_delta.cluster_results if language_delta is not None else ClusterResult()
        structural.by_language[language] = _structural_diff_for_language(
            language,
            old_clusters,
            new_result,
            members,
            diff_files,
            repo_dir,
            scope_id,
        )
    return structural


def _changeset_to_path_set(changes: ChangeSet) -> set[str]:
    """Collect every path in *changes*; renames contribute both old and new paths."""
    paths: set[str] = set()
    for fc in changes.files:
        paths.add(fc.file_path)
        if fc.old_path:
            paths.add(fc.old_path)
    return paths


def _structural_diff_for_language(
    language: str,
    old_clusters: dict[int, ClusterSnapshotEntry],
    new_result: ClusterResult,
    changed_members: set[str],
    diff_files: set[str],
    repo_dir: Path | None,
    scope_id: str,
) -> LanguageStructuralDiff:
    result = LanguageStructuralDiff(language=language)
    unattributed_files = _unattributed_changed_files(old_clusters, changed_members, diff_files, repo_dir)
    old_to_new: dict[int, set[int]] = {}
    new_to_old: dict[int, set[int]] = {}
    overlap_counts: dict[tuple[int, int], int] = {}

    for old_id, old_entry in old_clusters.items():
        for new_id, new_members in new_result.clusters.items():
            overlap = len(old_entry.members & new_members)
            if overlap == 0:
                continue
            old_to_new.setdefault(old_id, set()).add(new_id)
            new_to_old.setdefault(new_id, set()).add(old_id)
            overlap_counts[(old_id, new_id)] = overlap

    visited_old: set[int] = set()
    visited_new: set[int] = set()

    for start_old in sorted(old_to_new):
        if start_old in visited_old:
            continue
        component_old: set[int] = set()
        component_new: set[int] = set()
        old_queue = [start_old]
        new_queue: list[int] = []
        while old_queue or new_queue:
            while old_queue:
                old_id = old_queue.pop()
                if old_id in component_old:
                    continue
                component_old.add(old_id)
                for new_id in old_to_new.get(old_id, set()):
                    if new_id not in component_new:
                        new_queue.append(new_id)
            while new_queue:
                new_id = new_queue.pop()
                if new_id in component_new:
                    continue
                component_new.add(new_id)
                for old_id in new_to_old.get(new_id, set()):
                    if old_id not in component_old:
                        old_queue.append(old_id)

        visited_old |= component_old
        visited_new |= component_new
        if len(component_old) == 1 and len(component_new) == 1:
            old_id = next(iter(component_old))
            new_id = next(iter(component_new))
            member_delta = _build_member_delta(
                language,
                old_id,
                new_id,
                old_clusters[old_id],
                new_result,
                changed_members,
                diff_files,
                repo_dir,
                scope_id,
            )
            # Narrow file-level fallback: a member sitting in a changed file that produced
            # NO body-changed member anywhere (a module-level edit — top-level constant,
            # decorator, import) leaves no member signal, so gate on the file instead. Files
            # that DID have a body-changed member stay member-granular via ``dirty_members``.
            module_level_change = bool(member_delta.dirty_files & unattributed_files)
            if (
                member_delta.added_methods
                or member_delta.removed_methods
                or member_delta.dirty_members
                or module_level_change
            ):
                result.modified.append(member_delta)
            else:
                result.unchanged.append(member_delta)
            continue

        result.reshaped.append(
            _build_reshape(
                language,
                component_old,
                component_new,
                old_clusters,
                new_result,
                overlap_counts,
                changed_members,
                diff_files,
                repo_dir,
                scope_id,
            )
        )

    for old_id in sorted(set(old_clusters) - visited_old):
        result.removed.append(ClusterRef(language=language, cluster_id=old_id, scope_id=scope_id))
    for new_id in sorted(set(new_result.clusters) - visited_new):
        new_ref = ClusterRef(language=language, cluster_id=new_id, scope_id=scope_id)
        result.new.append(new_ref)
        result.new_details.append(
            _build_new_cluster_delta(
                language,
                new_id,
                new_result,
                changed_members,
                diff_files,
                repo_dir,
                scope_id,
            )
        )
    return result


def _build_new_cluster_delta(
    language: str,
    new_id: int,
    new_result: ClusterResult,
    changed_members: set[str],
    diff_files: set[str],
    repo_dir: Path | None,
    scope_id: str,
) -> ClusterMemberDelta:
    members = set(new_result.clusters.get(new_id, set()))
    files = {normalize_repo_path(file_path, repo_dir) for file_path in new_result.cluster_to_files.get(new_id, set())}
    if diff_files:
        files &= diff_files
    return ClusterMemberDelta(
        old_cluster=ClusterRef(language=language, cluster_id=new_id, scope_id=scope_id),
        new_cluster=ClusterRef(language=language, cluster_id=new_id, scope_id=scope_id),
        added_methods=members,
        dirty_members=members & changed_members,
        dirty_files=files,
    )


def _build_member_delta(
    language: str,
    old_id: int,
    new_id: int,
    old_entry: ClusterSnapshotEntry,
    new_result: ClusterResult,
    changed_members: set[str],
    diff_files: set[str],
    repo_dir: Path | None,
    scope_id: str,
) -> ClusterMemberDelta:
    new_members = new_result.clusters.get(new_id, set())
    return ClusterMemberDelta(
        old_cluster=ClusterRef(language=language, cluster_id=old_id, scope_id=scope_id),
        new_cluster=ClusterRef(language=language, cluster_id=new_id, scope_id=scope_id),
        unchanged_methods=old_entry.members & new_members,
        added_methods=new_members - old_entry.members,
        removed_methods=old_entry.members - new_members,
        dirty_members=(old_entry.members | new_members) & changed_members,
        dirty_files=_dirty_files(old_entry, new_result, new_id, diff_files, repo_dir),
    )


def _build_reshape(
    language: str,
    old_ids: set[int],
    new_ids: set[int],
    old_clusters: dict[int, ClusterSnapshotEntry],
    new_result: ClusterResult,
    overlap_counts: dict[tuple[int, int], int],
    changed_members: set[str],
    diff_files: set[str],
    repo_dir: Path | None,
    scope_id: str,
) -> ClusterReshape:
    old_refs = [ClusterRef(language=language, cluster_id=old_id, scope_id=scope_id) for old_id in sorted(old_ids)]
    new_refs = [ClusterRef(language=language, cluster_id=new_id, scope_id=scope_id) for new_id in sorted(new_ids)]
    ref_by_old_id = {ref.cluster_id: ref for ref in old_refs}
    ref_by_new_id = {ref.cluster_id: ref for ref in new_refs}
    ref_overlaps: dict[tuple[ClusterRef, ClusterRef], int] = {}
    for old_id, new_id in sorted(overlap_counts):
        if old_id in old_ids and new_id in new_ids:
            ref_overlaps[(ref_by_old_id[old_id], ref_by_new_id[new_id])] = overlap_counts[(old_id, new_id)]

    dirty_members: set[str] = set()
    dirty_files: set[str] = set()
    for old_id in old_ids:
        for new_id in new_ids:
            new_members = new_result.clusters.get(new_id, set())
            dirty_members |= (old_clusters[old_id].members | new_members) & changed_members
            dirty_files |= _dirty_files(old_clusters[old_id], new_result, new_id, diff_files, repo_dir)
    return ClusterReshape(
        old_clusters=old_refs,
        new_clusters=new_refs,
        overlap_counts=ref_overlaps,
        dirty_members=dirty_members,
        dirty_files=dirty_files,
    )


def _unattributed_changed_files(
    old_clusters: dict[int, ClusterSnapshotEntry],
    changed_members: set[str],
    diff_files: set[str],
    repo_dir: Path | None,
) -> set[str]:
    """Changed files whose edit produced no body-changed member — a module-level edit.

    A file in the diff that owns a body-changed member stays member-granular: only that
    member's own cluster is dirtied. A file with no body-changed member changed at module
    scope (a top-level constant, a decorator/plugin registration, an import) and adds no
    graph node, so it leaves no member-level signal; every cluster drawing a member from it
    must be re-examined. Returns the module-level subset of ``diff_files``.
    """
    if not diff_files:
        return set()
    member_files: dict[str, str] = {}
    for entry in old_clusters.values():
        for qname, path in entry.member_files.items():
            member_files[qname] = normalize_repo_path(path, repo_dir)
    attributed = {member_files[qname] for qname in changed_members if qname in member_files}
    return diff_files - attributed


def _dirty_files(
    old_entry: ClusterSnapshotEntry,
    new_result: ClusterResult,
    new_id: int,
    diff_files: set[str],
    repo_dir: Path | None,
) -> set[str]:
    if not diff_files:
        return set()
    cluster_files = (
        set(old_entry.files)
        | set(old_entry.member_files.values())
        | set(new_result.cluster_to_files.get(new_id, set()))
    )
    normalized = {normalize_repo_path(file_path, repo_dir) for file_path in cluster_files if file_path}
    return normalized & diff_files


def _delta_for_language(
    language: str,
    program_graph: ProgramGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    changed_members: set[str],
    diff_files: set[str] | None = None,
    repo_dir: Path | None = None,
) -> LanguageDelta:
    """Cluster the graph and report what moved against the prior partition.

    The clusterer warm-starts from ``program_graph.cluster_snapshot`` and maps its
    modules back onto the prior ids, so this only has to diff membership. An
    unchanged graph short-circuits inside the clusterer and reports no changes.
    """
    old_member_union = {qname for entry in old_clusters.values() for qname in entry.members}
    cluster_results = HierarchicalInfomapClusterer().cluster(program_graph)
    live_qnames = {qname for members in cluster_results.clusters.values() for qname in members}

    universe = live_qnames | old_member_union
    if not universe:
        return LanguageDelta(language=language, cluster_results=ClusterResult())

    added_nodes = live_qnames - old_member_union
    removed_nodes = old_member_union - live_qnames
    _log_diff_scope(language, program_graph, old_clusters, added_nodes, removed_nodes, diff_files, repo_dir)

    new_cluster_ids = set(cluster_results.clusters) - set(old_clusters)
    dropped_cluster_ids = set(old_clusters) - set(cluster_results.clusters)
    changed_cluster_ids = {
        cluster_id
        for cluster_id in set(cluster_results.clusters) & set(old_clusters)
        if cluster_results.clusters[cluster_id] != old_clusters[cluster_id].members
    }
    # Body-only edits leave membership identical; fold in surviving clusters that
    # own a body-changed member so has_changes fires and the owner is re-detailed.
    if changed_members:
        changed_cluster_ids |= {
            cluster_id
            for cluster_id, members in cluster_results.clusters.items()
            if cluster_id in old_clusters and members & changed_members
        }

    logger.info(
        "[cluster_delta] %s: added=%d removed=%d changed_members=%d; clusters new=%d changed=%d dropped=%d; "
        "changed_pct=%.3f",
        language,
        len(added_nodes),
        len(removed_nodes),
        len(changed_members & live_qnames),
        len(new_cluster_ids),
        len(changed_cluster_ids),
        len(dropped_cluster_ids),
        (len(added_nodes) + len(removed_nodes)) / len(universe),
    )
    return LanguageDelta(
        language=language,
        cluster_results=cluster_results,
        new_cluster_ids=new_cluster_ids,
        changed_cluster_ids=changed_cluster_ids,
        dropped_cluster_ids=dropped_cluster_ids,
    )


def _log_diff_scope(
    language: str,
    program_graph: ProgramGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    added_nodes: set[str],
    removed_nodes: set[str],
    diff_files: set[str] | None,
    repo_dir: Path | None,
) -> None:
    """Surface symbols that moved without their file appearing in the diff.

    Why: static analysis and change detection are independent, and when they
    disagree the partition is built on one story while the plan is built on the
    other. It is a diagnostic, not a gate — the clustering is already correct for
    the graph it was given.
    """
    if diff_files is None:
        return

    def old_file(qname: str) -> str | None:
        for entry in old_clusters.values():
            path = entry.member_files.get(qname)
            if path:
                return normalize_repo_path(path, repo_dir)
        return None

    unexplained_added = set()
    for qname in added_nodes:
        node = program_graph.nodes.get(qname)
        path = normalize_repo_path(node.file_path, repo_dir) if node and node.file_path else None
        if path is None or path not in diff_files:
            unexplained_added.add(qname)
    unexplained_removed = {q for q in removed_nodes if (old_file(q) or "") not in diff_files}

    if unexplained_added or unexplained_removed:
        logger.warning(
            "[cluster_delta] %s: %d added and %d removed symbols lie outside the source diff "
            "(first 10 added: %s; first 10 removed: %s)",
            language,
            len(unexplained_added),
            len(unexplained_removed),
            sorted(unexplained_added)[:10],
            sorted(unexplained_removed)[:10],
        )
