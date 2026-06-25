"""Deterministic cluster delta computation.

Mirrors ``build_all_cluster_results`` for the incremental path: produces a
``ClusterDelta`` describing which clusters carried over, which changed members,
which are entirely new, and which dropped — without requiring any LLM call.

Seeded Leiden: warm-start ``leidenalg`` with the prior partition as
``initial_membership`` and lock vertices outside the 1-hop affected frontier
via ``is_membership_fixed``. The basin-of-attraction property of warm-start
preserves cluster identity for vertices whose neighborhood didn't change,
while allowing the affected frontier to re-optimize freely (including pulling
existing nodes into newly-formed clusters with added nodes).
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from diagram_analysis.cluster_snapshot import ClusterSnapshot, ClusterSnapshotEntry
from diagram_analysis.io_utils import normalize_repo_path
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult
from static_analyzer.leiden_utils import find_partition_seeded

logger = logging.getLogger(__name__)


@dataclass
class LanguageDelta:
    language: str
    cluster_results: ClusterResult
    new_cluster_ids: set[int] = field(default_factory=set)
    changed_cluster_ids: set[int] = field(default_factory=set)
    dropped_cluster_ids: set[int] = field(default_factory=set)
    cluster_id_remap: dict[int, int] = field(default_factory=dict)

    @property
    def affected_cluster_ids(self) -> set[int]:
        return self.new_cluster_ids | self.changed_cluster_ids


@dataclass
class ClusterDelta:
    by_language: dict[str, LanguageDelta] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return any(d.affected_cluster_ids or d.dropped_cluster_ids for d in self.by_language.values())

    def all_affected_cluster_ids(self) -> set[int]:
        return {cid for d in self.by_language.values() for cid in d.affected_cluster_ids}

    def all_dropped_cluster_ids(self) -> set[int]:
        return {cid for d in self.by_language.values() for cid in d.dropped_cluster_ids}

    def cluster_results(self) -> dict[str, ClusterResult]:
        return {lang: d.cluster_results for lang, d in self.by_language.items()}

    def merged_cluster_id_remap(self) -> dict[int, int]:
        merged: dict[int, int] = {}
        for d in self.by_language.values():
            merged.update(d.cluster_id_remap)
        return merged


@dataclass(frozen=True)
class ClusterRef:
    language: str
    cluster_id: int
    scope_id: str = ""


@dataclass
class ClusterMemberDelta:
    old_cluster: ClusterRef
    new_cluster: ClusterRef
    unchanged_methods: set[str] = field(default_factory=set)
    added_methods: set[str] = field(default_factory=set)
    removed_methods: set[str] = field(default_factory=set)
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
) -> ClusterDelta:
    """Compute per-language cluster deltas via seeded Leiden.

    When ``changes`` is provided, qnames whose file is outside both the diff
    and the prior analysis are dropped as drift. Qnames in the prior analysis
    that vanish without appearing in the diff are kept but logged as
    inconsistent. ``repo_dir`` normalizes CFG-absolute paths to repo-relative
    posix so they match the diff. ``changes=None`` disables scoping.
    """
    delta = ClusterDelta()
    diff_files = _changeset_to_path_set(changes) if changes is not None else None
    for language in new_static.get_languages():
        cfg = new_static.get_cfg(language)
        nx_graph = cfg.to_networkx()
        old_clusters = old_snapshot.get_language(language)
        delta.by_language[language] = _delta_for_language(
            language,
            nx_graph,
            old_clusters,
            diff_files,
            repo_dir,
        )
    return delta


def structural_diff_from_delta(
    old_snapshot: ClusterSnapshot,
    delta: ClusterDelta,
    changes: ChangeSet | None = None,
    repo_dir: Path | None = None,
    scope_id: str = "",
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
            )
            if member_delta.added_methods or member_delta.removed_methods or member_delta.dirty_files:
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
) -> ClusterMemberDelta:
    new_members = new_result.clusters.get(new_id, set())
    return ClusterMemberDelta(
        old_cluster=ClusterRef(language=language, cluster_id=old_id, scope_id=scope_id),
        new_cluster=ClusterRef(language=language, cluster_id=new_id, scope_id=scope_id),
        unchanged_methods=old_entry.members & new_members,
        added_methods=new_members - old_entry.members,
        removed_methods=old_entry.members - new_members,
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


# @ivanmilevtues this is really important focus on it
def _delta_for_language(
    language: str,
    nx_graph: nx.DiGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    diff_files: set[str] | None = None,
    repo_dir: Path | None = None,
) -> LanguageDelta:
    # Why raw nodes (not a fresh clustering): seeded Leiden runs on the live
    # graph directly. Singletons in the live graph become added qnames; diff
    # scoping (if any) trims them to ones in changed files.
    live_qnames = set(nx_graph.nodes)
    old_member_union = {qname for entry in old_clusters.values() for qname in entry.members}

    universe = live_qnames | old_member_union
    if not universe:
        return LanguageDelta(language=language, cluster_results=ClusterResult())

    raw_added = live_qnames - old_member_union
    raw_removed = old_member_union - live_qnames

    inconsistent_removed: set[str] = set()
    if diff_files is not None:

        def _fresh_file(qname: str) -> str | None:
            attrs = nx_graph.nodes.get(qname)
            if attrs is None:
                return None
            file_path = attrs.get("file_path")
            return normalize_repo_path(file_path, repo_dir) if file_path else None

        def _old_file(qname: str) -> str | None:
            for entry in old_clusters.values():
                fp = entry.member_files.get(qname)
                if fp:
                    return normalize_repo_path(fp, repo_dir)
            return None

        scoped_added: set[str] = set()
        for qname in raw_added:
            file_path = _fresh_file(qname)
            if file_path is not None and file_path in diff_files:
                scoped_added.add(qname)
            # else: not-in analysis, not-in diff -- drift, drop.
        added_nodes = scoped_added

        # Removed quadrant: keep all (so Flavor B clears their clusters), but
        # surface the inconsistent ones in the log.
        for qname in raw_removed:
            file_path = _old_file(qname)
            if file_path is None or file_path not in diff_files:
                inconsistent_removed.add(qname)
        removed_nodes = raw_removed
    else:
        added_nodes = raw_added
        removed_nodes = raw_removed

    changed_pct = (len(added_nodes) + len(removed_nodes)) / len(universe)

    logger.info(
        "[cluster_delta] %s: seeded raw=(added=%d, removed=%d); "
        "diff-scoped=(added=%d, removed=%d); inconsistent=%d; changed_pct=%.3f",
        language,
        len(raw_added),
        len(raw_removed),
        len(added_nodes),
        len(removed_nodes),
        len(inconsistent_removed),
        changed_pct,
    )
    if added_nodes or removed_nodes:
        logger.info(
            "[cluster_delta] %s added qnames (first 20): %s; removed qnames (first 20): %s",
            language,
            sorted(added_nodes)[:20],
            sorted(removed_nodes)[:20],
        )
    if inconsistent_removed:
        logger.warning(
            "[cluster_delta] %s removed qnames outside source diff (inconsistent, %d total, first 10): %s",
            language,
            len(inconsistent_removed),
            sorted(inconsistent_removed)[:10],
        )
    return _flavor_b_seeded(language, nx_graph, old_clusters, added_nodes, removed_nodes)


# ---------------------------------------------------------------------------
# Flavor B (seeded Leiden via leidenalg)
# ---------------------------------------------------------------------------
def _flavor_b_seeded(
    language: str,
    nx_graph: nx.DiGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    added_nodes: set[str],
    removed_nodes: set[str],
) -> LanguageDelta:
    """Seeded Leiden with the prior partition as initial state and the non-frontier vertices locked.

    Why: see module docstring — identity comes from ``initial_membership``'s
    basin of attraction plus the hard ``is_membership_fixed`` guarantee.
    """
    if nx_graph.number_of_nodes() == 0:
        return LanguageDelta(
            language=language,
            cluster_results=ClusterResult(strategy="incremental_seeded_empty"),
            dropped_cluster_ids=set(old_clusters.keys()),
        )

    qname_to_prior_cid: dict[str, int] = {}
    for cid, entry in old_clusters.items():
        for qname in entry.members:
            if qname not in removed_nodes and qname in nx_graph:
                qname_to_prior_cid[qname] = cid

    # Drop drift qnames (in graph, not in any prior cluster, not in added) from
    # the working subgraph — they aren't tracked changes and shouldn't cluster.
    tracked_qnames: set[str] = set(qname_to_prior_cid.keys()) | (added_nodes & set(nx_graph.nodes))
    if not tracked_qnames:
        return LanguageDelta(
            language=language,
            cluster_results=ClusterResult(strategy="incremental_seeded_empty"),
            dropped_cluster_ids=set(old_clusters.keys()),
        )
    working_graph = nx_graph.subgraph(tracked_qnames)

    surviving_prior_cids = sorted(set(qname_to_prior_cid.values()))
    prior_to_compact: dict[int, int] = {cid: i for i, cid in enumerate(surviving_prior_cids)}

    # initial_membership must align with igraph vertex order, which mirrors
    # nx_graph.nodes() iteration order. Use the same iteration to build it.
    idx_to_qname: list[str] = list(working_graph.nodes())
    next_compact = len(surviving_prior_cids)
    initial_compact: list[int] = []
    for qname in idx_to_qname:
        prior_cid = qname_to_prior_cid.get(qname)
        if prior_cid is not None:
            initial_compact.append(prior_to_compact[prior_cid])
        else:
            initial_compact.append(next_compact)
            next_compact += 1

    affected = _affected_frontier(working_graph, old_clusters, added_nodes, removed_nodes)
    is_fixed = [qname not in affected for qname in idx_to_qname]

    n_total = len(idx_to_qname)
    n_locked = sum(is_fixed)
    logger.info(
        "[cluster_delta] %s seeded: tracked=%d, affected=%d (%.1f%%), locked=%d, " "old_clusters=%d, prior_carried=%d",
        language,
        n_total,
        len(affected),
        100.0 * len(affected) / n_total if n_total else 0.0,
        n_locked,
        len(old_clusters),
        len(surviving_prior_cids),
    )

    try:
        membership = find_partition_seeded(
            working_graph,
            initial_membership_compact=initial_compact,
            is_membership_fixed=is_fixed,
            seed=42,
        )
    except Exception as e:
        # Degrade to all-singletons rather than crash the pipeline; reconciliation handles any shape.
        logger.warning(
            f"[cluster_delta] {language}: seeded Leiden failed ({e}); falling back to all-singleton membership."
        )
        membership = list(range(len(idx_to_qname)))

    leiden_clusters: dict[int, set[str]] = {}
    for v_idx, lcid in enumerate(membership):
        leiden_clusters.setdefault(lcid, set()).add(idx_to_qname[v_idx])

    cluster_id_remap, new_cluster_ids, changed_cluster_ids, dropped_cluster_ids, final_clusters = (
        _reconcile_seeded_partition(leiden_clusters, old_clusters)
    )
    _absorb_new_file_overlap_clusters(
        final_clusters,
        old_clusters,
        new_cluster_ids,
        changed_cluster_ids,
        added_nodes,
        nx_graph,
    )

    cluster_results = _materialize_cluster_result(final_clusters, working_graph, "incremental_seeded")
    return LanguageDelta(
        language=language,
        cluster_results=cluster_results,
        new_cluster_ids=new_cluster_ids,
        changed_cluster_ids=changed_cluster_ids,
        dropped_cluster_ids=dropped_cluster_ids,
        cluster_id_remap=cluster_id_remap,
    )


def _affected_frontier(
    nx_graph: nx.Graph | nx.DiGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    added_nodes: set[str],
    removed_nodes: set[str],
    *,
    hops: int = 1,
) -> set[str]:
    """Vertices whose cluster boundary may legitimately want to shift.

    Why both directions on a DiGraph: callers and callees of an added node both
    have a changed neighborhood. For removed nodes (no longer in nx_graph),
    surviving cluster-mates lost a co-member and are added directly.
    """
    frontier: set[str] = set(added_nodes) & set(nx_graph.nodes)
    seed_set = frontier.copy()
    is_directed = nx_graph.is_directed()
    for _ in range(hops):
        next_layer: set[str] = set()
        for qname in seed_set:
            if qname not in nx_graph:
                continue
            if is_directed:
                next_layer.update(nx_graph.predecessors(qname))
                next_layer.update(nx_graph.successors(qname))
            else:
                next_layer.update(nx_graph.neighbors(qname))
        next_layer -= frontier
        frontier |= next_layer
        seed_set = next_layer
        if not seed_set:
            break

    if removed_nodes:
        for entry in old_clusters.values():
            if entry.members & removed_nodes:
                frontier.update(m for m in entry.members - removed_nodes if m in nx_graph)

    return frontier & set(nx_graph.nodes)


def _reconcile_seeded_partition(
    leiden_clusters: dict[int, set[str]],
    old_clusters: dict[int, ClusterSnapshotEntry],
) -> tuple[dict[int, int], set[int], set[int], set[int], dict[int, set[str]]]:
    """Map leiden's renumbered output IDs back onto prior IDs by greedy max-overlap.

    Leftover leiden clusters get fresh IDs; leftover prior clusters tombstone
    into ``dropped_cluster_ids``. Returns ``(cluster_id_remap, new_cluster_ids,
    changed_cluster_ids, dropped_cluster_ids, final_clusters)``.
    """
    overlap_pairs: list[tuple[int, int, int]] = []  # (overlap, leiden_cid, prior_cid)
    for lcid, members in leiden_clusters.items():
        for prior_cid, entry in old_clusters.items():
            shared = len(members & entry.members)
            if shared > 0:
                overlap_pairs.append((shared, lcid, prior_cid))
    overlap_pairs.sort(reverse=True)

    leiden_to_prior: dict[int, int] = {}
    used_prior: set[int] = set()
    for _, lcid, prior_cid in overlap_pairs:
        if lcid in leiden_to_prior or prior_cid in used_prior:
            continue
        leiden_to_prior[lcid] = prior_cid
        used_prior.add(prior_cid)

    next_new_id = max(old_clusters.keys(), default=0) + 1
    final_clusters: dict[int, set[str]] = {}
    new_cluster_ids: set[int] = set()
    changed_cluster_ids: set[int] = set()
    cluster_id_remap: dict[int, int] = {}

    for lcid, members in leiden_clusters.items():
        if lcid in leiden_to_prior:
            prior_cid = leiden_to_prior[lcid]
            final_clusters[prior_cid] = members
            cluster_id_remap[prior_cid] = prior_cid
            if old_clusters[prior_cid].members != members:
                changed_cluster_ids.add(prior_cid)
        else:
            new_id = next_new_id
            next_new_id += 1
            final_clusters[new_id] = members
            new_cluster_ids.add(new_id)

    dropped_cluster_ids = {cid for cid in old_clusters.keys() if cid not in used_prior}

    return cluster_id_remap, new_cluster_ids, changed_cluster_ids, dropped_cluster_ids, final_clusters


def _absorb_new_file_overlap_clusters(
    final_clusters: dict[int, set[str]],
    old_clusters: dict[int, ClusterSnapshotEntry],
    new_cluster_ids: set[int],
    changed_cluster_ids: set[int],
    added_nodes: set[str],
    nx_graph: nx.DiGraph,
) -> None:
    """Merge all-added new clusters into the one old cluster sharing their file."""
    for new_id in sorted(list(new_cluster_ids)):
        members = final_clusters.get(new_id, set())
        if not members or not members <= added_nodes:
            continue
        files = _files_for_members(members, nx_graph)
        candidates: list[tuple[int, int]] = []
        for old_id, old_entry in old_clusters.items():
            if old_id not in final_clusters:
                continue
            old_files = set(old_entry.files) | set(old_entry.member_files.values())
            overlap = len(files & old_files)
            if overlap:
                candidates.append((overlap, old_id))
        if not candidates:
            continue
        candidates.sort(reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            continue
        target_id = candidates[0][1]
        final_clusters[target_id] |= members
        del final_clusters[new_id]
        new_cluster_ids.remove(new_id)
        changed_cluster_ids.add(target_id)


def _files_for_members(members: set[str], nx_graph: nx.DiGraph) -> set[str]:
    files: set[str] = set()
    for qname in members:
        attrs = nx_graph.nodes.get(qname, {})
        file_path = attrs.get("file_path")
        if file_path:
            files.add(file_path)
    return files


def _materialize_cluster_result(
    member_sets: dict[int, set[str]],
    nx_graph: nx.DiGraph,
    strategy: str,
) -> ClusterResult:
    clusters: dict[int, set[str]] = {}
    cluster_to_files: dict[int, set[str]] = {}
    file_to_clusters: dict[str, set[int]] = {}
    for cid, members in member_sets.items():
        if not members:
            continue
        clusters[cid] = set(members)
        files: set[str] = set()
        for qname in members:
            attrs = nx_graph.nodes.get(qname, {})
            file_path = attrs.get("file_path")
            if file_path:
                files.add(file_path)
                file_to_clusters.setdefault(file_path, set()).add(cid)
        cluster_to_files[cid] = files
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=file_to_clusters,
        strategy=strategy,
    )
