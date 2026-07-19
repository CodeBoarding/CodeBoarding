"""Structural deltas for stable root and lineage-seeded child Infomap clusters.

Root incremental analysis compares persisted Infomap lineage with the updated
``ProgramGraph``. Child scopes reuse recorded method ancestry when they build
their scoped Infomap results.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
import json
from pathlib import Path

from agents.scope_ids import ROOT_SCOPE_ID
from agents.content_hash import SourceCache, hash_method_body, read_source_lines
from agents.file_index_models import FileEntry
from diagram_analysis.cluster_snapshot import ClusterSnapshot, ClusterSnapshotEntry
from diagram_analysis.io_utils import normalize_repo_path
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results
from static_analyzer.clustering import ClusterResult
from static_analyzer.program_graph import ProgramEdge, ProgramEdgeKind, ProgramGraph

logger = logging.getLogger(__name__)


class GraphChangeStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"


@dataclass(frozen=True)
class GraphEdgeChange:
    """One changed typed edge, attributed to its primary owning cluster."""

    status: GraphChangeStatus
    kind: ProgramEdgeKind
    source: str
    target: str
    primary_cluster_id: int
    related_cluster_ids: tuple[int, ...] = ()

    def llm_str(self) -> str:
        related = f"; related_clusters={list(self.related_cluster_ids)}" if self.related_cluster_ids else ""
        return (
            f"{self.status.value} {self.kind.value}: {self.source} -> {self.target}; "
            f"primary_cluster={self.primary_cluster_id}{related}"
        )


@dataclass
class LanguageDelta:
    language: str
    cluster_results: ClusterResult
    new_cluster_ids: set[int] = field(default_factory=set)
    changed_cluster_ids: set[int] = field(default_factory=set)
    dropped_cluster_ids: set[int] = field(default_factory=set)
    modified_methods_by_cluster: dict[int, set[str]] = field(default_factory=dict)
    edge_changes_by_cluster: dict[int, list[GraphEdgeChange]] = field(default_factory=dict)

    @property
    def affected_cluster_ids(self) -> set[int]:
        return (
            self.new_cluster_ids
            | self.changed_cluster_ids
            | set(self.modified_methods_by_cluster)
            | set(self.edge_changes_by_cluster)
        )


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
    modified_methods: set[str] = field(default_factory=set)
    edge_changes: list[GraphEdgeChange] = field(default_factory=list)
    dirty_files: set[str] = field(default_factory=set)


@dataclass
class ClusterReshape:
    old_clusters: list[ClusterRef] = field(default_factory=list)
    new_clusters: list[ClusterRef] = field(default_factory=list)
    overlap_counts: dict[tuple[ClusterRef, ClusterRef], int] = field(default_factory=dict)
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
    old_static: StaticAnalysisResults | None = None,
    previous_files: dict[str, FileEntry] | None = None,
) -> ClusterDelta:
    """Compare the prior stable Infomap partition with the updated ProgramGraph."""
    delta = ClusterDelta()
    _inherit_global_cluster_namespace(old_static, new_static)
    current_results = build_all_cluster_results(new_static)
    for language in sorted(set(old_snapshot.by_language) | set(current_results)):
        old_clusters = old_snapshot.get_language(language)
        current = current_results.get(language, ClusterResult())
        old_ids = set(old_clusters)
        current_ids = set(current.clusters)
        current_graph = _program_graph(new_static, language)
        old_graph = _program_graph(old_static, language) if old_static is not None else None
        modified_methods = _modified_methods_by_cluster(
            current_graph,
            current,
            previous_files or {},
            changes,
            repo_dir,
        )
        edge_changes = _edge_changes_by_cluster(
            old_graph,
            current_graph,
            old_clusters,
            current,
            changes,
            repo_dir,
        )
        delta.by_language[language] = LanguageDelta(
            language=language,
            cluster_results=current,
            new_cluster_ids=current_ids - old_ids,
            changed_cluster_ids={
                cluster_id
                for cluster_id in old_ids & current_ids
                if old_clusters[cluster_id].members != current.clusters[cluster_id]
            },
            dropped_cluster_ids=old_ids - current_ids,
            modified_methods_by_cluster=modified_methods,
            edge_changes_by_cluster=edge_changes,
        )
    return delta


def _inherit_global_cluster_namespace(
    old_static: StaticAnalysisResults | None,
    new_static: StaticAnalysisResults,
) -> None:
    """Migrate global IDs from pre-namespace Infomap cache copies."""
    if old_static is None:
        return
    old_graphs = old_static.available_program_graphs()
    for language, current_graph in new_static.available_program_graphs().items():
        old_graph = old_graphs.get(language)
        if old_graph is None or old_graph.cluster_snapshot is None or current_graph.cluster_snapshot is None:
            continue
        old_mapping = dict(getattr(old_graph.cluster_snapshot, "global_cluster_ids", {}))
        current_mapping = dict(getattr(current_graph.cluster_snapshot, "global_cluster_ids", {}))
        if not old_mapping or current_mapping:
            continue
        current_graph.cluster_snapshot.global_cluster_ids = old_mapping
        current_graph.cluster_snapshot.next_global_cluster_id = int(
            getattr(
                old_graph.cluster_snapshot,
                "next_global_cluster_id",
                max(old_mapping.values(), default=0) + 1,
            )
        )


def structural_diff_from_delta(
    old_snapshot: ClusterSnapshot,
    delta: ClusterDelta,
    changes: ChangeSet | None = None,
    repo_dir: Path | None = None,
    scope_id: str = ROOT_SCOPE_ID,
) -> StructuralClusterDiff:
    """Classify seeded cluster output into scope-local structural facts."""
    diff_files = _changeset_to_path_set(changes) if changes is not None else set()
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
            diff_files,
            repo_dir,
            scope_id,
            language_delta,
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
    diff_files: set[str],
    repo_dir: Path | None,
    scope_id: str,
    language_delta: LanguageDelta | None = None,
) -> LanguageStructuralDiff:
    result = LanguageStructuralDiff(language=language)
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
                diff_files,
                repo_dir,
                scope_id,
                language_delta,
            )
            if (
                member_delta.added_methods
                or member_delta.removed_methods
                or member_delta.modified_methods
                or member_delta.edge_changes
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
        dirty_files=files,
    )


def _build_member_delta(
    language: str,
    old_id: int,
    new_id: int,
    old_entry: ClusterSnapshotEntry,
    new_result: ClusterResult,
    diff_files: set[str],
    repo_dir: Path | None,
    scope_id: str,
    language_delta: LanguageDelta | None = None,
) -> ClusterMemberDelta:
    new_members = new_result.clusters.get(new_id, set())
    return ClusterMemberDelta(
        old_cluster=ClusterRef(language=language, cluster_id=old_id, scope_id=scope_id),
        new_cluster=ClusterRef(language=language, cluster_id=new_id, scope_id=scope_id),
        unchanged_methods=old_entry.members & new_members,
        added_methods=new_members - old_entry.members,
        removed_methods=old_entry.members - new_members,
        modified_methods=set(
            language_delta.modified_methods_by_cluster.get(new_id, set()) if language_delta is not None else set()
        ),
        edge_changes=list(language_delta.edge_changes_by_cluster.get(new_id, []) if language_delta is not None else []),
        dirty_files=_dirty_files(old_entry, new_result, new_id, diff_files, repo_dir),
    )


def _build_reshape(
    language: str,
    old_ids: set[int],
    new_ids: set[int],
    old_clusters: dict[int, ClusterSnapshotEntry],
    new_result: ClusterResult,
    overlap_counts: dict[tuple[int, int], int],
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

    dirty_files: set[str] = set()
    for old_id in old_ids:
        for new_id in new_ids:
            dirty_files |= _dirty_files(old_clusters[old_id], new_result, new_id, diff_files, repo_dir)
    return ClusterReshape(
        old_clusters=old_refs,
        new_clusters=new_refs,
        overlap_counts=ref_overlaps,
        dirty_files=dirty_files,
    )


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


def _program_graph(static_analysis: StaticAnalysisResults | None, language: str) -> ProgramGraph | None:
    if static_analysis is None:
        return None
    return static_analysis.available_program_graphs().get(language)


def _modified_methods_by_cluster(
    graph: ProgramGraph | None,
    cluster_result: ClusterResult,
    previous_files: dict[str, FileEntry],
    changes: ChangeSet | None,
    repo_dir: Path | None,
) -> dict[int, set[str]]:
    """Map proven method-body changes to their stable Infomap clusters."""
    if graph is None or changes is None or repo_dir is None or not previous_files:
        return {}
    changed_paths = _changeset_to_path_set(changes)
    owner = {
        qualified_name: cluster_id
        for cluster_id, members in cluster_result.clusters.items()
        for qualified_name in members
    }
    previous_methods = {
        (file_path, method.qualified_name): method
        for file_path, entry in previous_files.items()
        for method in entry.methods
    }
    source_cache: SourceCache = {}
    impacted: dict[int, set[str]] = {}
    for node in graph.symbol_nodes():
        file_path = normalize_repo_path(node.file_path, repo_dir)
        if file_path not in changed_paths:
            continue
        previous = previous_methods.get((file_path, node.id))
        cluster_id = owner.get(node.id)
        if previous is None or cluster_id is None or not previous.content_hash:
            continue
        source_lines = read_source_lines(repo_dir, file_path, source_cache)
        current_hash = hash_method_body(source_lines, node.line_start, node.line_end)
        if current_hash and current_hash != previous.content_hash:
            impacted.setdefault(cluster_id, set()).add(node.id)
    return impacted


def _edge_changes_by_cluster(
    old_graph: ProgramGraph | None,
    current_graph: ProgramGraph | None,
    old_clusters: dict[int, ClusterSnapshotEntry],
    current_result: ClusterResult,
    changes: ChangeSet | None,
    repo_dir: Path | None,
) -> dict[int, list[GraphEdgeChange]]:
    """Attribute typed graph changes only to the endpoint rooted in changed source."""
    if old_graph is None or current_graph is None or changes is None:
        return {}
    changed_paths = _changeset_to_path_set(changes)
    old_result = _cluster_result_from_snapshot(old_clusters)
    old_edges = {_edge_key(edge): edge for edge in old_graph.edges}
    current_edges = {_edge_key(edge): edge for edge in current_graph.edges}
    impacts: dict[int, list[GraphEdgeChange]] = {}

    for edge_key in sorted(set(old_edges) | set(current_edges), key=lambda key: (key[0].value, key[1], key[2])):
        old_edge = old_edges.get(edge_key)
        current_edge = current_edges.get(edge_key)
        if old_edge is None:
            status = GraphChangeStatus.ADDED
            edge = current_edge
            graph = current_graph
            result = current_result
        elif current_edge is None:
            status = GraphChangeStatus.REMOVED
            edge = old_edge
            graph = old_graph
            result = old_result
        elif _edge_state(old_edge) != _edge_state(current_edge):
            status = GraphChangeStatus.MODIFIED
            edge = current_edge
            graph = current_graph
            result = current_result
        else:
            continue
        assert edge is not None

        primary_node_id = edge.target if edge.kind == ProgramEdgeKind.CONTAINS else edge.source
        primary_node = graph.nodes.get(primary_node_id)
        if primary_node is None:
            continue
        primary_file = normalize_repo_path(primary_node.file_path, repo_dir)
        if not primary_file or primary_file not in changed_paths:
            continue
        primary_cluster = _cluster_for_node(graph, result, primary_node_id)
        if primary_cluster is None:
            continue

        related = {
            cluster_id
            for endpoint in (edge.source, edge.target)
            if (cluster_id := _cluster_for_node(graph, result, endpoint)) is not None and cluster_id != primary_cluster
        }
        impacts.setdefault(primary_cluster, []).append(
            GraphEdgeChange(
                status=status,
                kind=edge.kind,
                source=edge.source,
                target=edge.target,
                primary_cluster_id=primary_cluster,
                related_cluster_ids=tuple(sorted(related)),
            )
        )
    return impacts


def _edge_key(edge: ProgramEdge) -> tuple[ProgramEdgeKind, str, str]:
    return edge.kind, edge.source, edge.target


def _edge_state(edge: ProgramEdge) -> tuple[int, str]:
    # Absolute call-site lines move under unrelated edits. Count and typed
    # metadata capture semantic edge changes without turning line drift into
    # an architectural update.
    occurrence_count = len(edge.occurrences)
    metadata = json.dumps(edge.metadata, sort_keys=True, default=str)
    return occurrence_count, metadata


def _cluster_result_from_snapshot(entries: dict[int, ClusterSnapshotEntry]) -> ClusterResult:
    clusters = {cluster_id: set(entry.members) for cluster_id, entry in entries.items()}
    cluster_to_files = {cluster_id: set(entry.files) for cluster_id, entry in entries.items()}
    file_to_clusters: dict[str, set[int]] = {}
    for cluster_id, files in cluster_to_files.items():
        for file_path in files:
            file_to_clusters.setdefault(file_path, set()).add(cluster_id)
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=file_to_clusters,
        strategy="baseline_infomap",
    )


def _cluster_for_node(
    graph: ProgramGraph,
    cluster_result: ClusterResult,
    node_id: str,
) -> int | None:
    direct = [cluster_id for cluster_id, members in cluster_result.clusters.items() if node_id in members]
    if direct:
        return min(direct)

    snapshot = graph.cluster_snapshot
    if snapshot is not None:
        path = snapshot.node_paths.get(node_id, ())
        if path:
            module_members = snapshot.module_members.get(path[0], set())
            owners: dict[int, int] = {}
            for cluster_id, members in cluster_result.clusters.items():
                overlap = len(module_members & members)
                if overlap:
                    owners[cluster_id] = overlap
            if owners:
                return max(owners, key=lambda cluster_id: (owners[cluster_id], -cluster_id))

    node = graph.nodes.get(node_id)
    if node is not None and node.file_path:
        candidates = cluster_result.get_clusters_for_file(node.file_path)
        if candidates:
            return min(candidates)
    return None
