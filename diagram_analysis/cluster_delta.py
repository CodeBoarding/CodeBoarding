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


def compute_cluster_delta(
    old_snapshot: ClusterSnapshot,
    new_static: StaticAnalysisResults,
    changes: ChangeSet | None = None,
    repo_dir: Path | None = None,
) -> ClusterDelta:
    """Compute per-language cluster deltas via seeded Leiden.

    When ``changes`` is provided, the cluster delta filters drift noise:
    qnames whose file is outside the source diff AND outside the prior analysis
    are dropped, since neither side considers them tracked changes. Qnames in
    the prior analysis that vanish without their file appearing in the diff
    are kept (the LLM should reason about the inconsistency) but logged as a
    warning so drift is visible.

    ``repo_dir`` is used to normalize CFG-absolute paths down to repo-relative
    posix so they can be compared against the diff's repo-relative paths.

    When ``changes`` is ``None`` (e.g., GitHub Action callers without a diff
    source), no scoping is applied — current behavior.
    """
    delta = ClusterDelta()
    diff_files = _changeset_to_path_set(changes) if changes is not None else None
    for language in new_static.get_languages():
        cfg = new_static.get_cfg(language)
        nx_graph = cfg.to_networkx()
        old_clusters = old_snapshot.get_language(language)
        delta.by_language[language] = _delta_for_language(language, nx_graph, old_clusters, diff_files, repo_dir)
    return delta


def _changeset_to_path_set(changes: ChangeSet) -> set[str]:
    """Collect every path mentioned by a ChangeSet — including renames' old paths.

    Renames are an edge case: ``FileChange.file_path`` is the new path,
    ``FileChange.old_path`` the old. A qname under the old path that
    survives in the fresh CFG would otherwise look like drift; including
    both sides keeps that case in the diff scope.
    """
    paths: set[str] = set()
    for fc in changes.files:
        paths.add(fc.file_path)
        if fc.old_path:
            paths.add(fc.old_path)
    return paths


def _delta_for_language(
    language: str,
    nx_graph: nx.DiGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    diff_files: set[str] | None = None,
    repo_dir: Path | None = None,
) -> LanguageDelta:
    # Universe is the prior cluster members plus the live CFG nodes. We use
    # the raw graph nodes (not a fresh clustering) because seeded Leiden runs
    # on the live graph directly — there is no separate "fresh clustering"
    # step. Singleton/noise nodes in the live graph become added qnames here;
    # diff scoping (when enabled) filters them down to ones in changed files.
    live_qnames = set(nx_graph.nodes)
    old_member_union = {qname for entry in old_clusters.values() for qname in entry.members}

    universe = live_qnames | old_member_union
    if not universe:
        return LanguageDelta(language=language, cluster_results=ClusterResult())

    raw_added = live_qnames - old_member_union
    raw_removed = old_member_union - live_qnames

    # Diff scoping: see compute_cluster_delta docstring. We drop only the
    # quadrant (qname not-in prior analysis AND file not-in diff) — pure
    # drift on untracked qnames in untracked files. Quadrant (qname
    # in-prior-analysis AND file not-in diff) is preserved as inconsistent
    # state (logged as warning).
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
    # Sample of the qnames going into / out of the universe — small enough to
    # log every run, useful for diagnosing "why is the LLM call firing on a
    # pure deletion" scenarios where added/removed counts disagree with the
    # user-visible diff.
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
    """Seeded Leiden on the full graph with prior partition as initial state and frontier locked.

    Why: see module docstring. Identity preservation comes from the basin-of-
    attraction effect of ``initial_membership`` plus the hard guarantee of
    ``is_membership_fixed`` on vertices outside the affected frontier.
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

    # Why restrict the working graph: drift qnames (in nx_graph but not in any
    # prior cluster and filtered out of added_nodes by diff scoping) shouldn't
    # be clustered at all. The previous procedure ignored them implicitly by
    # only routing added_nodes; the seeded path needs to drop them explicitly.
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

    # Why ordered nodes matter: leidenalg.Optimiser requires
    # initial_membership aligned with igraph vertex order, which comes from
    # ig.Graph.from_networkx (which iterates nx_graph.nodes()). Build the
    # alignment via the same iteration order on the (subgraph) we'll cluster.
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
        logger.warning(
            f"[cluster_delta] {language}: seeded Leiden failed ({e}); " "falling back to all-singleton membership."
        )
        # Why: if Leiden ever raises, we'd rather degrade to "everyone is their
        # own community" than crash the pipeline. The reconciliation step below
        # handles arbitrary cluster shapes.
        membership = list(range(len(idx_to_qname)))

    leiden_clusters: dict[int, set[str]] = {}
    for v_idx, lcid in enumerate(membership):
        leiden_clusters.setdefault(lcid, set()).add(idx_to_qname[v_idx])

    leiden_clusters = _absorb_orphans_by_file(leiden_clusters, working_graph)

    cluster_id_remap, new_cluster_ids, changed_cluster_ids, dropped_cluster_ids, final_clusters = (
        _reconcile_seeded_partition(leiden_clusters, old_clusters)
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

    Why both directions on directed graphs: ``nx_graph.neighbors`` on a
    DiGraph returns out-neighbors only; we need callers AND callees of an
    added node since both have a changed neighborhood. For removed nodes
    (gone from nx_graph), the "neighbors" we care about are the surviving
    cluster-mates that lost a co-member.
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


def _absorb_orphans_by_file(
    clusters: dict[int, set[str]],
    nx_graph: nx.Graph | nx.DiGraph,
) -> dict[int, set[str]]:
    """Merge zero-edge singleton clusters into a same-file cluster when one exists.

    Why: nodes with no edges have no graph signal Leiden can use to place them.
    The previous Flavor B used file co-location for this case; we preserve that
    behavior here as a thin post-processor over Leiden's output rather than
    losing user-visible placement quality.
    """
    if not clusters:
        return clusters
    qname_to_cid: dict[str, int] = {q: cid for cid, members in clusters.items() for q in members}
    singleton_cids = [cid for cid, members in clusters.items() if len(members) == 1]
    if not singleton_cids:
        return clusters

    result = {cid: set(members) for cid, members in clusters.items()}
    for cid in singleton_cids:
        if cid not in result:
            continue
        (qname,) = result[cid]
        if qname in nx_graph and nx_graph.degree(qname) > 0:
            continue  # not orphaned; Leiden's choice stands
        file_path = nx_graph.nodes[qname].get("file_path") if qname in nx_graph else None
        if not file_path:
            continue
        target_cid: int | None = None
        target_count = 0
        for other_cid, other_members in result.items():
            if other_cid == cid:
                continue
            count = sum(1 for m in other_members if m in nx_graph and nx_graph.nodes[m].get("file_path") == file_path)
            if count > target_count:
                target_count = count
                target_cid = other_cid
        if target_cid is not None:
            result[target_cid].add(qname)
            qname_to_cid[qname] = target_cid
            del result[cid]
    return result


def _reconcile_seeded_partition(
    leiden_clusters: dict[int, set[str]],
    old_clusters: dict[int, ClusterSnapshotEntry],
) -> tuple[dict[int, int], set[int], set[int], set[int], dict[int, set[str]]]:
    """Map leiden's renumbered output IDs back onto old prior IDs by overlap.

    Why: leidenalg renumbers communities to a contiguous 0..k-1 range in the
    output, so ID continuity across runs has to be reconstructed. We greedily
    pair leiden output clusters with the prior cluster they overlap with most;
    leftover leiden clusters get fresh IDs (max(old) + i + 1), leftover old
    clusters get tombstoned in dropped_cluster_ids.

    Returns: (cluster_id_remap, new_cluster_ids, changed_cluster_ids,
    dropped_cluster_ids, final_clusters_keyed_by_new_ids).
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
