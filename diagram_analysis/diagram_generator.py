import json
import logging
import os
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
from langchain_core.language_models import BaseChatModel

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    MetaAnalysisInsights,
    Relation,
    SourceCodeReference,
    index_components_by_id,
)
from agents.details_agent import DetailsAgent
from agents.incremental_agent import (
    IncrementalAgent,
    prune_empty_components,
    remove_deleted_files,
)
from agents.incremental_results import RecursiveScopeUpdateResult
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from agents.llm_config import initialize_llms
from agents.llm_errors import LLMAuthError
from agents.meta_agent import MetaAgent
from agents.planner_agent import component_is_separable, get_expandable_components, leaf_load
from agents.relation_edges import index_relation_endpoints, preserve_unchanged_relations
from agents.scope_ids import ROOT_SCOPE_ID
from agents.content_hash import SourceCache, hash_repo_source_files, tree_hash_from_file_hashes
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.cluster_delta import (
    ChangedMembers,
    ClusterDelta,
    LanguageDelta,
    StructuralClusterDiff,
    compute_changed_members,
    compute_cluster_delta,
    structural_diff_from_delta,
)
from diagram_analysis.cluster_snapshot import ClusterSnapshot, snapshot_from_static_analysis
from diagram_analysis.exceptions import IncrementalCacheMissingError, ScopeContainmentError
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.file_index import build_files_index, refresh_method_spans_from_cfg
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis, write_fingerprint
from repo_utils.path_utils import normalize_repo_path
from diagram_analysis.scope_plan import plan_scope_result_update, plan_scope_update
from diagram_analysis.tree_shape import absorb_single_child_components
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import get_monitoring_run_dir
from repo_utils.change_detector import ChangeSet
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer, get_static_analysis
from static_analyzer.cfg import CallGraph, DEFAULT_REFERENCE_KINDS
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolver import StaticReferenceResolver
from static_analyzer.cluster_relations import (
    build_global_node_to_component_map,
    build_global_relations,
    build_owner_index,
    is_self_or_descendant,
    prune_ungrounded_edges,
)
from static_analyzer.config import Language
from static_analyzer.clustering import (
    ClusterResult,
    ClusterScopeResult,
    ClusteringService,
)
from static_analyzer.scanner import ProjectScanner
from telemetry.events import track_analysis

logger = logging.getLogger(__name__)


def _component_depth(component_id: str | None) -> int:
    """Return the absolute diagram depth for a hierarchical component id."""
    if not component_id:
        return 1
    return component_id.count(".") + 1


def _component_expansion_seeds(components: list[Component], max_depth: int) -> list[tuple[Component, int]]:
    """Return components that may still be expanded, paired with absolute depth."""
    return [
        (component, depth)
        for component in components
        if (depth := _component_depth(component.component_id)) < max_depth
    ]


def _member_keys(component: Component) -> frozenset[tuple[str, str]]:
    """The ``(file_path, qualified_name)`` set a component owns — its membership identity."""
    return frozenset(
        (group.file_path, method.qualified_name) for group in component.file_methods for method in group.methods
    )


def _owned_method_keys(components: Iterable[Component]) -> set[tuple[str, str]]:
    """The ``(file_path, qualified_name)`` set the given components collectively own."""
    return {key for component in components for key in _member_keys(component)}


def _key_entity_is_owned(
    entity: SourceCodeReference,
    member_keys: set[tuple[str, str]],
    repo_dir: Path,
) -> bool:
    """Return whether a key entity still names one of the component's methods."""
    if entity.reference_file is None:
        return any(qualified_name == entity.qualified_name for _path, qualified_name in member_keys)
    entity_key = (normalize_repo_path(entity.reference_file, repo_dir), entity.qualified_name)
    normalized_keys = {(normalize_repo_path(path, repo_dir), qualified_name) for path, qualified_name in member_keys}
    return entity_key in normalized_keys


def _reconcile_child_scope(
    parent: Component,
    child_scope: AnalysisInsights,
    parent_keys: set[tuple[str, str]],
    child_keys: set[tuple[str, str]],
    repo_dir: Path,
) -> None:
    """Bring a child scope's membership up to its parent's, preserving unchanged placements.

    ``update_scope`` may shift a handful of methods into or out of a parent. Re-clustering
    the whole subtree to absorb that would renumber sub-components nothing touched, so
    instead drop only the departed methods — the double-ownership fix — and graft each
    entered method onto the child component with the strongest same-file affinity, leaving
    every method that stayed exactly where it was.
    """
    departed = child_keys - parent_keys
    entered = parent_keys - child_keys
    if departed:
        for child in child_scope.components:
            had_methods = any(group.methods for group in child.file_methods)
            for group in child.file_methods:
                group.methods = [m for m in group.methods if (group.file_path, m.qualified_name) not in departed]
            child.file_methods = [group for group in child.file_methods if group.methods]
            remaining = set(_member_keys(child))
            child.key_entities = [
                entity for entity in child.key_entities if _key_entity_is_owned(entity, remaining, repo_dir)
            ]
            if had_methods and not remaining:
                child.source_cluster_ids = []
    if entered:
        parent_methods = {
            (group.file_path, method.qualified_name): method
            for group in parent.file_methods
            for method in group.methods
        }
        _graft_entered_methods(child_scope, entered, parent_methods)
    if departed:
        # Membership moved, so a scoped relation may cite a method that just left. Those are
        # consumed as authoritative by ``build_global_relations``, so they must go — but only
        # THEY must. Clearing the whole scope left the next step with no baseline to compare
        # against, so `preserve_unchanged_relations` was skipped outright and every relation in
        # the scope came back re-worded: measured on `referenced-symbol-deleted`, scopes 3 and
        # 3.2 arrived with 0 baseline pairs and produced 8 re-wordings whose call sets were
        # byte-identical. Relations that never mention a departed method are kept so their
        # wording can be carried forward.
        departed_qnames = {qualified_name for _path, qualified_name in departed}
        child_scope.components_relations = [
            relation
            for relation in child_scope.components_relations
            if not any(
                edge.source.qualified_name in departed_qnames or edge.target.qualified_name in departed_qnames
                for edge in [*relation.key_edges, *relation.all_edges]
            )
        ]
    child_scope.files = build_files_index(child_scope, repo_dir)


def _graft_entered_methods(
    child_scope: AnalysisInsights,
    entered: set[tuple[str, str]],
    parent_methods: dict[tuple[str, str], MethodEntry],
) -> None:
    """Place each entered method on the child component that already owns most of its file."""
    file_owner_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    child_by_id: dict[str, Component] = {}
    for child in child_scope.components:
        child_by_id[child.component_id] = child
        for group in child.file_methods:
            file_owner_counts[group.file_path][child.component_id] += len(group.methods)
    # Deterministic home for a method whose file no child owns yet.
    fallback = max(
        child_scope.components,
        key=lambda c: (sum(len(g.methods) for g in c.file_methods), c.component_id),
    )
    for file_path, qualified_name in sorted(entered):
        counts = file_owner_counts.get(file_path)
        target = child_by_id[max(counts, key=lambda cid: (counts[cid], cid))] if counts else fallback
        _append_method(target, file_path, parent_methods[(file_path, qualified_name)])
        file_owner_counts[file_path][target.component_id] += 1


def _append_method(component: Component, file_path: str, method: MethodEntry) -> None:
    for group in component.file_methods:
        if group.file_path == file_path:
            if all(existing.qualified_name != method.qualified_name for existing in group.methods):
                group.methods.append(method)
            return
    component.file_methods.append(FileMethodGroup(file_path=file_path, methods=[method]))


@dataclass
class _ComponentBaseline:
    """One component's pre-update metadata and membership, for verbatim restoration."""

    name: str
    description: str
    key_entities: list[SourceCodeReference]
    source_group_names: list[str]
    source_cluster_ids: list[str]
    member_keys: frozenset[tuple[str, str]]
    member_qnames: frozenset[str]


@dataclass
class _MembershipBaseline:
    """Pre-update snapshot the incremental restores unchanged components from."""

    meta_by_id: dict[str, _ComponentBaseline] = field(default_factory=dict)
    # sub-scope_id -> a verbatim deep copy of the child-scope analysis, so a component with
    # no changed member anywhere in its subtree can have its whole sub-component structure
    # (which method sits in which child) restored, not just its top-level ownership.
    scope_by_id: dict[str, AnalysisInsights] = field(default_factory=dict)


def _iter_incremental_scopes(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> Iterator[tuple[str, AnalysisInsights]]:
    """Yield ``(scope_id, analysis)`` for the root and every expanded sub-scope."""
    yield ROOT_SCOPE_ID, root_analysis
    for scope_id, sub in sub_analyses.items():
        yield scope_id, sub


def _capture_baseline_member_keys(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, frozenset[tuple[str, str]]]:
    """Per-component ``{id -> frozenset((file, qname))}`` — the member-key half of the baseline.

    Captured before ``remove_deleted_files`` so a deleted method still shows as a membership
    change at save-time relation preservation. Deliberately lighter than
    ``_capture_membership_baseline`` (no deep copies of scopes/metadata), which the restore
    passes need captured *after* the scrub so they don't re-inject a deleted method.
    """
    keys: dict[str, frozenset[tuple[str, str]]] = {}
    for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        for component in analysis.components:
            if not component.component_id:
                continue
            keys[component.component_id] = _member_keys(component)
    return keys


def _capture_membership_baseline(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> _MembershipBaseline:
    """Snapshot per-scope method ownership and per-component metadata before the update.

    The clustering hierarchy repairs ownership before it is applied. This snapshot retains
    component metadata and unchanged child scopes that can be restored verbatim afterward.
    """
    baseline = _MembershipBaseline()
    for scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        if scope_id != ROOT_SCOPE_ID:
            baseline.scope_by_id[scope_id] = analysis.model_copy(deep=True)
        for component in analysis.components:
            if not component.component_id:
                continue
            keys: set[tuple[str, str]] = set()
            qnames: set[str] = set()
            for group in component.file_methods:
                for method in group.methods:
                    key = (group.file_path, method.qualified_name)
                    keys.add(key)
                    qnames.add(method.qualified_name)
            baseline.meta_by_id[component.component_id] = _ComponentBaseline(
                name=component.name,
                description=component.description,
                key_entities=[entity.model_copy(deep=True) for entity in component.key_entities],
                source_group_names=list(component.source_group_names),
                source_cluster_ids=list(component.source_cluster_ids),
                member_keys=frozenset(keys),
                member_qnames=frozenset(qnames),
            )
    return baseline


def _restore_unchanged_metadata(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline: _MembershipBaseline,
    changed_members: set[str],
    changed_files: set[str],
) -> set[str]:
    """Restore name/description/key_entities of components identical to their baseline.

    A component with the same membership as the baseline, no body-changed member, and no
    module-level edit in a file it owns did not genuinely change; the planner may still have
    reworded it. Restoring its metadata and returning its id lets the caller drop it from the
    refresh set, so its relations to other unchanged components are carried over verbatim
    rather than re-derived.
    """
    unchanged_ids: set[str] = set()
    for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        for component in analysis.components:
            meta = baseline.meta_by_id.get(component.component_id)
            if meta is None:
                continue
            final_keys = _member_keys(component)
            owns_changed_file = any(group.file_path in changed_files for group in component.file_methods)
            # Ignore membership changes caused only by symbols the commit added or deleted.
            drifted = {key for key in (final_keys ^ meta.member_keys) if key[1] not in changed_members}
            if drifted:
                continue
            # Membership is baseline-identical, so everything DERIVED FROM MEMBERSHIP is restored
            # even when a body inside the component changed. Cluster ids are the repartition's
            # own numbering for the same set of methods, and key entities are a choice among
            # those methods — editing one method's body makes neither of them wrong. Gating both
            # on "owns a changed file" re-authored them for every component that merely shares a
            # file with the edit: measured on `referenced-symbol-deleted`, 11 components changed
            # cluster ids and 3 changed key entities for a commit that deleted one function.
            if final_keys or not meta.member_keys:
                component.source_cluster_ids = list(meta.source_cluster_ids)
            # An entity survives unless its symbol is GONE. Editing a method's body does not
            # make the class it lives in a worse choice of key entity — dropping on "changed"
            # rather than "deleted" is why `File` and `LazyFile` vanished from three components
            # when one method inside them was edited.
            live_qnames = {qname for _path, qname in final_keys}
            component.key_entities = [
                entity.model_copy(deep=True) for entity in meta.key_entities if entity.qualified_name in live_qnames
            ]
            if (meta.member_qnames & changed_members) or owns_changed_file:
                continue
            # Prose is held to the stricter gate: a component whose code changed may honestly
            # want re-describing, and only a component nothing touched is safe to call unchanged.
            component.name = meta.name
            component.description = meta.description
            component.source_group_names = list(meta.source_group_names)
            unchanged_ids.add(component.component_id)
    return unchanged_ids


def _fully_unchanged_component_ids(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline: _MembershipBaseline,
    changed_members: set[str],
    changed_files: set[str],
    protected_ids: set[str],
) -> set[str]:
    """Ids of components whose entire subtree is byte-identical to the baseline.

    A component qualifies when, at every depth, no member changed, no file it owns had a
    module-level edit, and it neither gained nor lost a member. Containment (parent is a
    superset of every descendant) means a component's own top-level member set already spans
    its whole subtree, so testing that set is enough: no member qname is in ``changed_members``,
    no owned file is in ``changed_files``, and the live keys equal the baseline. A subtree
    holding a freshly created component is excluded — restoring it verbatim would delete that
    component, and new components are never restored.
    """
    fully_unchanged: set[str] = set()
    for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        for component in analysis.components:
            cid = component.component_id
            meta = baseline.meta_by_id.get(cid)
            if meta is None or meta.member_qnames & changed_members:
                continue
            if any(group.file_path in changed_files for group in component.file_methods):
                continue
            if any(is_self_or_descendant(protected_id, cid) for protected_id in protected_ids):
                continue
            if _member_keys(component) == meta.member_keys:
                fully_unchanged.add(cid)
    return fully_unchanged


def _restore_unchanged_subtrees(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline: _MembershipBaseline,
    changed_members: set[str],
    changed_files: set[str],
    protected_ids: set[str],
) -> set[str]:
    """Restore the whole child-scope subtree of every fully-unchanged component, verbatim.

    The hierarchy repairs ownership at every clustered scope, but an entirely untouched
    descendant scope need not be rebuilt at all. Restore its baseline deep copy so metadata
    and child boundaries remain byte-for-byte stable.

    Returns the full set of preserved ids so the caller can skip them in the reconcile pass;
    the restore itself only rewrites each maximal subtree once (restoring a root already
    covers its descendants).
    """
    fully_unchanged = _fully_unchanged_component_ids(
        root_analysis, sub_analyses, baseline, changed_members, changed_files, protected_ids
    )
    for scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        # A component whose parent scope is also fully unchanged is restored by that parent.
        if scope_id != ROOT_SCOPE_ID and scope_id in fully_unchanged:
            continue
        for component in analysis.components:
            cid = component.component_id
            if cid not in fully_unchanged:
                continue
            for descendant_id, baseline_scope in baseline.scope_by_id.items():
                if is_self_or_descendant(descendant_id, cid):
                    sub_analyses[descendant_id] = baseline_scope.model_copy(deep=True)
    return fully_unchanged


def _incremental_changed_component_ids(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline_component_ids: set[str],
    baseline_member_keys: dict[str, frozenset[tuple[str, str]]],
    changed_members: set[str],
    changed_files: set[str],
) -> set[str]:
    """Component ids whose global relations may legitimately differ from the baseline.

    A live component counts as changed when it is absent from the baseline (freshly
    created), owns a body-changed member, owns a file with a module-level edit no member
    represents (``changed_files``), or its live member-key set differs from the baseline —
    it gained or lost a member. Membership churn alone (a new caller of another component,
    or the last caller removed) relabels the edges between the two components even with no
    surviving body-hash change, so it must be treated as changed or the genuinely-new edge
    is dropped / the stale baseline edge restored. Because every ancestor scope lists a
    method in its own ``file_methods``, the owner of a change and all of its ancestors are
    captured together. Everything else is preserved verbatim.
    """
    # Files whose edit some member already accounts for. The file-level signal exists for a
    # MODULE-level edit no member represents — an import, a constant, a docstring — so applying
    # it to a file that also has a changed member flags every other co-owner of that file as
    # changed too. Measured on `referenced-symbol-deleted`: components 1, 3, 3.1, 3.2 and 4.2
    # own none of the five changed methods and most have identical membership, yet all were
    # marked changed because they co-own `types.py`/`utils.py` with the component that does.
    # That is what opens the label gate for their relations and re-words them.
    represented_files = {
        group.file_path
        for _scope, analysis in _iter_incremental_scopes(root_analysis, sub_analyses)
        for component in analysis.components
        for group in component.file_methods
        if any(method.qualified_name in changed_members for method in group.methods)
    }
    unrepresented_files = changed_files - represented_files

    changed: set[str] = set()
    for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        for component in analysis.components:
            component_id = component.component_id
            if not component_id:
                continue
            live_keys = _member_keys(component)
            body_changed = any(
                method.qualified_name in changed_members for group in component.file_methods for method in group.methods
            )
            file_changed = any(group.file_path in unrepresented_files for group in component.file_methods)
            # Membership counts as changed only when the difference involves a member the
            # commit itself touched. Re-clustering moves untouched methods between components
            # all the time; treating that as a change put components owning nothing the commit
            # reached into `changed_ids`, and their relations were then freely added and removed.
            drift = live_keys ^ baseline_member_keys.get(component_id, frozenset())
            membership_changed = any(qualified_name in changed_members for _path, qualified_name in drift)
            if component_id not in baseline_component_ids or body_changed or file_changed or membership_changed:
                changed.add(component_id)
    return changed


class DiagramGenerator:
    def __init__(
        self,
        repo_location: Path,
        temp_folder: Path,
        repo_name: str,
        output_dir: Path,
        depth_level: int,
        run_id: str,
        log_path: str,
        project_name: str | None = None,
        monitoring_enabled: bool = False,
        static_analyzer: StaticAnalyzer | None = None,
        changes: ChangeSet | None = None,
    ):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir
        self.depth_level = depth_level
        self.project_name = project_name
        self.run_id = run_id
        self.log_path = log_path
        self.monitoring_enabled = monitoring_enabled
        self.force_full_analysis = False  # Set to True to skip incremental updates
        # Source-tree changeset for the iterative path. When set, the cluster
        # delta drops drift qnames whose file is outside the diff AND outside
        # the prior analysis (see ``compute_cluster_delta``). ``None`` runs
        # unscoped (no drift filtering).
        self.changes: ChangeSet | None = changes
        # Qnames whose method body changed vs the baseline, derived once per incremental run.
        # Ownership repair is independent of this signal; it drives metadata refresh and lets
        # the save-time global relation rebuild identify components the commit actually changed.
        self._changed_members: set[str] = set()
        # Changed files whose edit no hashed member represents (module-level/config content).
        # A component owning one of these counts as changed even with no body-hash or membership
        # change, so its metadata and global relations are re-derived, not carried over stale.
        self._changed_unattributed_files: set[str] = set()
        # Incremental-only baseline captured at the top of ``generate_analysis_incremental``,
        # so the save-time global relation rebuild can carry an edge between two unchanged
        # components over verbatim instead of re-deriving (and re-labelling) it.
        # ``None`` => full analysis: rebuild every relation. Keyed by ``(src_id, dst_id)``.
        self._baseline_global_relations: dict[tuple[str, str], Relation] | None = None
        self._baseline_component_ids: set[str] = set()
        # Per-component baseline member-key set, captured with the membership baseline. A
        # component whose live member keys differ from these gained or lost a member (without
        # necessarily a body-hash change), so its relations may legitimately relabel — the
        # save-time global rebuild must treat it as changed.
        self._baseline_member_keys: dict[str, frozenset[tuple[str, str]]] = {}
        # Whole-tree content hash, stamped into the pkl's sibling .sha file as the
        # diff base for the next warm-start (NOT a cache gate). ``prepare_analysis``
        # fills it from the live tree when unset; ``None`` is a tag-less save.
        self.source_sha: str | None = None
        # Whole-tree ``{posix_path: sha16}`` fingerprint, computed once per run and
        # reused for source_sha, the sidecar, and every save's source_tree_hash
        # instead of re-walking the tree each time.
        self._source_tree_fingerprint: dict[str, str] | None = None
        self._static_analyzer = static_analyzer

        self.details_agent: DetailsAgent | None = None
        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.clustering_hierarchy: ClusterScopeResult | None = None
        self.abstraction_agent: AbstractionAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.incremental_agent: IncrementalAgent | None = None
        self.meta_context: MetaAnalysisInsights | None = None
        self.file_coverage_data: dict | None = None

        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None
        # Separability verdict per component member set. Traversal asks once per
        # component and every save asks again for the whole tree; the subgraph build
        # plus Leiden sweep behind each answer is the expensive part of the
        # deterministic pipeline. Keyed by membership, so a changed component re-runs.
        self._separable_cache: dict[frozenset[tuple[str, str]], bool] = {}
        self._analysis_start_time = time.time()

    @track_analysis
    def process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        return self._process_component(component)

    def deterministic_analysis(
        self,
        *,
        hierarchy_depth: int | None = None,
        target_component: Component | None = None,
        require_incremental_baseline: bool = False,
    ) -> None:
        """Run source fingerprinting, static analysis, and deterministic clustering."""
        self._analysis_start_time = time.time()
        # Fingerprint the whole tree once; source_sha, the sidecar, and every
        # save's source_tree_hash reuse it instead of re-walking per call.
        self._source_tree_fingerprint = hash_repo_source_files(self.repo_location)
        # Compute the source-state tag from live source when a caller didn't
        # supply one, so the pkl always gets a .sha sibling for the next
        # warm-start — no caller has to thread source_sha in.
        if self.source_sha is None:
            self.source_sha = self._source_tree_hash() or None

        if self._static_analyzer is not None:
            logger.info("Using injected StaticAnalyzer (clients already running)")
            static_analysis = self._get_static_with_injected_analyzer()
        else:
            static_analysis = self._get_static_with_new_analyzer()

        self.static_analysis = static_analysis
        if require_incremental_baseline:
            baseline = static_analysis.incremental_base_results
            if baseline is None or not snapshot_from_static_analysis(baseline).all_cluster_ids():
                error = IncrementalCacheMissingError(self.output_dir)
                logger.error("%s", error)
                raise error

        depth = hierarchy_depth if hierarchy_depth is not None else self.depth_level
        self.clustering_hierarchy = ClusteringService().build_full_hierarchy(static_analysis, depth)
        if target_component is not None:
            self._register_component_scope(target_component, depth)

        # --- Capture Static Analysis Stats ---
        static_stats: dict[str, Any] = {"repo_name": self.repo_name, "languages": {}}
        scanner = ProjectScanner(self.repo_location)
        loc_by_language = {pl.language: pl.size for pl in scanner.scan()}
        for language in static_analysis.get_languages():
            files = static_analysis.get_source_files(language)
            static_stats["languages"][language] = {
                "file_count": len(files),
                "lines_of_code": loc_by_language.get(language, 0),
            }

        # Build file coverage data from scanner's all_text_files and analyzed files
        self.file_coverage_data = self._build_file_coverage(scanner, static_analysis)

        self._run_health_report(static_analysis)

        if self.monitoring_enabled:
            monitoring_dir = get_monitoring_run_dir(self.log_path, create=True)
            logger.debug(f"Monitoring enabled. Writing stats to {monitoring_dir}")

            # Save code_stats.json
            code_stats_file = monitoring_dir / "code_stats.json"
            with open(code_stats_file, "w", encoding="utf-8") as f:
                json.dump(static_stats, f, indent=2)
            logger.debug(f"Written code_stats.json to {code_stats_file}")

    def agent_init(self) -> None:
        """Initialize the LLM-backed agents after deterministic analysis."""
        assert self.static_analysis is not None
        agent_llm, parsing_llm = initialize_llms()
        self._initialize_meta_agent(agent_llm, parsing_llm)
        assert self.meta_agent is not None
        meta_context = self.meta_agent.analyze_project_metadata(skip_cache=self.force_full_analysis)
        self.meta_context = meta_context
        self._initialize_agents(self.static_analysis, meta_context, agent_llm, parsing_llm)

        if self.monitoring_enabled:
            monitoring_dir = get_monitoring_run_dir(self.log_path, create=True)
            self.stats_writer = StreamingStatsWriter(
                monitoring_dir=monitoring_dir,
                agents_dict=self._monitoring_agents,
                repo_name=self.project_name or self.repo_name,
                output_dir=str(self.output_dir),
                start_time=self._analysis_start_time,
            )

    def prepare_analysis(
        self,
        *,
        hierarchy_depth: int | None = None,
        target_component: Component | None = None,
        require_incremental_baseline: bool = False,
    ) -> None:
        """Prepare deterministic inputs, then initialize the analysis agents."""
        self.deterministic_analysis(
            hierarchy_depth=hierarchy_depth,
            target_component=target_component,
            require_incremental_baseline=require_incremental_baseline,
        )
        self.agent_init()

    def _component_separable(self, component: Component) -> bool:
        """Deterministic gate: should this component be split into sub-components?

        A component past the leaf ceiling is split whatever its call structure
        says — it is too big to read as one box, and that verdict needs no
        subgraph. Otherwise the component's own subgraph decides, against a bar
        that eases as the component grows. If the subgraph can't be built (e.g. a
        legacy static-analysis baseline whose pickled edges predate the current
        schema), fall back to the structural default of expanding rather than
        aborting the run.

        Memoized on the component's member set: traversal asks once per component
        and every save asks again for the whole tree, and the answer depends on
        nothing else. A component whose membership changed (including one pruned
        by ``_strip_ignored``) gets a different key and is re-evaluated.
        """
        assert self.details_agent is not None
        load = leaf_load(component)
        if load >= 1.0:
            logger.info(f"[Planner] Component '{component.name}' is past the leaf ceiling (load {load:.2f}); expanding")
            return True
        key = _member_keys(component)
        if key in self._separable_cache:
            return self._separable_cache[key]
        try:
            cluster_results, subgraph_cfgs = self.details_agent._create_strict_component_subgraph(component)
        except Exception:
            logger.exception("Separability check failed for '%s'; defaulting to expandable", component.name)
            return True
        if not cluster_results:
            separable = False
        else:
            # Reference-augmented graph, matching the production split: a component
            # separable only via CONTAINS/INHERITS edges
            # must not be judged cohesive on a call-only graph.
            cfg_graphs = {lang: cfg.to_networkx(DEFAULT_REFERENCE_KINDS) for lang, cfg in subgraph_cfgs.items()}
            separable = component_is_separable(cluster_results, cfg_graphs, load)
        self._separable_cache[key] = separable
        return separable

    def _expandable_ids_for_tree(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> tuple[list[str] | None, dict[str, list[str]] | None]:
        """The run's own expandable sets for the root scope and each sub-scope.

        Persisting these keeps a component the separability gate kept as a leaf from
        being re-advertised as expandable by the save-time recompute, which is
        structural-only. ``(None, None)`` when the details agent isn't live (a bare
        re-save), leaving the save to its deterministic default rather than crashing.

        A component that already holds an analysed subtree is expandable by definition —
        the subtree is right there. Re-litigating it here can only destroy it: the save
        serializes children only for a component it is told is expandable, so a verdict
        that flips to False discards work already done and, because analysis.json is the
        store, the subtree is gone for good. Such a component is therefore added
        unconditionally, outside ``get_expandable_components`` — its structural gate runs
        before the separability one, so a predicate cannot rescue a component the
        structural gate has already rejected.
        """
        if self.details_agent is None:
            return None, None

        clustering_groups = self.clustering_hierarchy.clustering_groups if self.clustering_hierarchy else {}
        if clustering_groups:

            def precomputed_ids(scope: AnalysisInsights) -> list[str]:
                return [
                    component.component_id
                    for component in scope.components
                    if component.component_id
                    and (
                        component.component_id in sub_analyses
                        or ((group := clustering_groups.get(component.component_id)) is not None and group.expandable)
                    )
                ]

            return precomputed_ids(root_analysis), {
                scope_id: precomputed_ids(scope) for scope_id, scope in sub_analyses.items()
            }

        def expandable_ids(scope: AnalysisInsights, parent_had_clusters: bool = True) -> list[str]:
            ids = [
                component.component_id
                for component in get_expandable_components(
                    scope, parent_had_clusters=parent_had_clusters, separable=self._component_separable
                )
                if component.component_id
            ]
            chosen = set(ids)
            ids.extend(
                component.component_id
                for component in scope.components
                if component.component_id and component.component_id in sub_analyses
                if component.component_id not in chosen
            )
            return ids

        root_ids = expandable_ids(root_analysis)
        component_lookup = index_components_by_id(root_analysis, sub_analyses)
        sub_ids: dict[str, list[str]] = {}
        for cid, sub in sub_analyses.items():
            parent = component_lookup.get(cid)
            sub_ids[cid] = expandable_ids(sub, parent_had_clusters=bool(parent.source_cluster_ids) if parent else True)
        return root_ids, sub_ids

    def _process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        """Process a single component and return its name, sub-analysis, and new components to analyze."""
        try:
            assert self.details_agent is not None

            preclustered_scopes = self.clustering_hierarchy.preclustered_scopes if self.clustering_hierarchy else {}
            scope = preclustered_scopes.get(component.component_id)
            if scope is None:
                raise ValueError(f"No precomputed clustering scope for component {component.component_id}")
            analysis, _ = self.details_agent.run(scope, component)
            new_components = [child for child in analysis.components if child.component_id in preclustered_scopes]

            return component.component_id, analysis, new_components
        except LLMAuthError:
            # A rejected key fails every component identically; don't swallow it
            # per-component and grind through the rest — abort the whole run.
            raise
        except Exception as e:
            logging.error(f"Error processing component {component.name}: {e}")
            return None, None, []

    def _run_health_report(self, static_analysis: StaticAnalysisResults) -> None:
        """Run health checks and write the report to the output directory."""
        health_config_dir = Path(self.output_dir) / "health"
        initialize_health_dir(health_config_dir)
        health_config = load_health_config(health_config_dir)

        health_report = run_health_checks(
            static_analysis,
            self.repo_name,
            config=health_config,
            repo_path=self.repo_location,
        )
        if health_report is not None:
            health_path = Path(self.output_dir) / "health" / "health_report.json"
            with open(health_path, "w", encoding="utf-8") as f:
                f.write(health_report.model_dump_json(indent=2, exclude_none=True))
            logger.info(f"Health report written to {health_path} (score: {health_report.overall_score:.3f})")
        else:
            logger.warning("Health checks skipped: no languages found in static analysis results")

    def _strip_ignored(
        self,
        analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
    ) -> None:
        """Sweep ``.codeboardingignore``-matched files out of the rendered tree.

        Single chokepoint applied right before every ``save_analysis(...)`` so
        the serialized architecture honors the user's ignore rules, regardless
        of which discovery path (LSP imports, agent clustering, plugin) added
        a file. Other layers (file_monitor, file_coverage, function_size)
        already use ``RepoIgnoreManager``; this extends the same authority to
        the analyzer's persisted output.

        Idempotent. Mutates in place. Empty components are kept (relations may
        reference them); downstream renderers handle zero-method components.
        """
        ignore_manager = RepoIgnoreManager(self.repo_location)
        ignore_manager.strip_ignored(analysis)
        for sub in (sub_analyses or {}).values():
            ignore_manager.strip_ignored(sub)

    def _build_file_coverage(self, scanner: ProjectScanner, static_analysis: StaticAnalysisResults) -> dict:
        """Build file coverage data comparing all text files against analyzed files."""
        ignore_manager = RepoIgnoreManager(self.repo_location)
        coverage = FileCoverage(self.repo_location, ignore_manager)

        # Convert to Path objects for set operations
        all_files = {Path(f) for f in scanner.all_text_files}
        analyzed_files = {Path(f) for f in static_analysis.get_all_source_files()}

        return coverage.build(all_files, analyzed_files)

    def _write_file_coverage(self) -> None:
        """Write file_coverage.json to output directory."""
        if not self.file_coverage_data:
            return

        report = FileCoverageReport(
            version=1,
            generated_at=datetime.now(timezone.utc).isoformat(),
            analyzed_files=self.file_coverage_data["analyzed_files"],
            not_analyzed_files=[NotAnalyzedFile(**entry) for entry in self.file_coverage_data["not_analyzed_files"]],
            summary=FileCoverageSummary(**self.file_coverage_data["summary"]),
        )

        coverage_path = Path(self.output_dir) / "file_coverage.json"
        with open(coverage_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2, exclude_none=True))
        logger.info(f"File coverage report written to {coverage_path}")

    def _changed_files_for_static_analysis(self) -> set[Path] | None:
        """Absolute changed-file paths from the incremental ChangeSet, or None.

        Incremental analysis always carries a git-free ``ChangeSet`` (the
        fingerprint diff). We hand those files to the static-analysis warm-start
        so it re-LSPs exactly them without shelling out to git. None means "no
        ChangeSet" (a full run) and leaves the warm-start to its own git scoping;
        an empty set means "incremental, nothing changed" and correctly re-LSPs
        zero files instead of falling back to a full re-LSP via git.
        """
        if self.changes is None:
            return None
        rel_paths = self.changes.added_files + self.changes.modified_files + self.changes.deleted_files
        return {(self.repo_location / rel).resolve() for rel in rel_paths}

    def _get_static_with_injected_analyzer(self) -> StaticAnalysisResults:
        """Run the injected analyzer with the configured cache policy."""
        assert self._static_analyzer is not None
        disable_reuse = os.getenv("CODEBOARDING_DISABLE_CACHE_REUSE", "").lower() in ("1", "true", "yes")
        skip_cache = self.force_full_analysis or disable_reuse
        if self.force_full_analysis:
            logger.info("Force full analysis: skipping static analysis cache")
        if disable_reuse:
            logger.info("CODEBOARDING_DISABLE_CACHE_REUSE set; skipping static analysis cache")
        self._static_analyzer.changed_files = self._changed_files_for_static_analysis()
        result = self._static_analyzer.analyze(
            skip_cache=skip_cache,
            source_sha=self.source_sha,
            cache_dir=self.output_dir,
        )
        result.diagnostics = self._static_analyzer.collected_diagnostics
        return result

    def _get_static_with_new_analyzer(self) -> StaticAnalysisResults:
        """Run static analysis with a newly created analyzer."""
        disable_reuse = os.getenv("CODEBOARDING_DISABLE_CACHE_REUSE", "").lower() in ("1", "true", "yes")
        skip_cache = self.force_full_analysis or disable_reuse
        if self.force_full_analysis:
            logger.info("Force full analysis: skipping static analysis cache")
        if disable_reuse:
            logger.info("CODEBOARDING_DISABLE_CACHE_REUSE set; skipping static analysis cache")
        return get_static_analysis(
            self.repo_location,
            skip_cache=skip_cache,
            source_sha=self.source_sha,
            cache_dir=self.output_dir,
            changed_files=self._changed_files_for_static_analysis(),
        )

    def _seed_incremental_cluster_cache(self, cluster_results: dict[str, ClusterResult]) -> None:
        """Write post-delta ``cluster_results`` into each language's ``ClusterCache``.

        On the incremental path the abstraction agent doesn't run, so the live
        partition has to be plumbed in explicitly before ``stop_clients`` saves
        the pkl. ``cluster_snapshot`` reads exclusively from this cache.
        """
        if self.static_analysis is None:
            return
        for language, cr in cluster_results.items():
            try:
                self.static_analysis.get_clusters(Language(language)).adopt(cr)
            except (ValueError, KeyError):
                continue

    def _persist_static_analysis_artifact(self) -> None:
        """Persist the post-clustering static-analysis artifact."""
        if self._static_analyzer is not None:
            self._static_analyzer.flush_cache()
            return
        if self.static_analysis is None:
            return
        StaticAnalysisCache(self.output_dir, self.repo_location).save(self.static_analysis, source_sha=self.source_sha)

    def _source_tree_fingerprint_map(self) -> dict[str, str]:
        """The whole-tree fingerprint, fingerprinting on first use if preparation didn't."""
        if self._source_tree_fingerprint is None:
            self._source_tree_fingerprint = hash_repo_source_files(self.repo_location)
        return self._source_tree_fingerprint

    def _source_tree_hash(self) -> str:
        """The source-tree version key aggregated from the cached fingerprint."""
        return tree_hash_from_file_hashes(self._source_tree_fingerprint_map())

    def _initialize_meta_agent(self, agent_llm: BaseChatModel, parsing_llm: BaseChatModel) -> None:
        """Initialize the metadata agent needed before the other agents."""
        self.meta_agent = MetaAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            run_id=self.run_id,
        )
        self._monitoring_agents["MetaAgent"] = self.meta_agent

    def _initialize_agents(
        self,
        static_analysis: StaticAnalysisResults,
        meta_context: MetaAnalysisInsights,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ) -> None:
        """Initialize agents that depend on static analysis and project metadata."""
        self.details_agent = DetailsAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        self.abstraction_agent = AbstractionAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        self.incremental_agent = IncrementalAgent(
            repo_dir=self.repo_location,
            static_analysis=static_analysis,
            project_name=self.repo_name,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            changes=self.changes,
        )
        self._monitoring_agents.update(
            {
                "DetailsAgent": self.details_agent,
                "AbstractionAgent": self.abstraction_agent,
                "IncrementalAgent": self.incremental_agent,
            }
        )

    def _register_component_scope(self, component: Component, hierarchy_depth: int) -> None:
        """Precompute an exact hierarchy rooted at one persisted component ID."""
        assert self.static_analysis is not None
        assert self.clustering_hierarchy is not None
        member_keys = {
            (normalize_repo_path(file_path, self.repo_location), qualified_name)
            for file_path, qualified_name in _member_keys(component)
        }
        graphs: dict[str, CallGraph] = {}
        for language, graph in self.static_analysis.available_cfgs().items():
            owned_names = {
                qualified_name
                for qualified_name, node in graph.nodes.items()
                if (normalize_repo_path(node.file_path, self.repo_location), qualified_name) in member_keys
            }
            if not owned_names:
                continue
            scoped_graph = graph.filter_by_nodes(owned_names)
            if scoped_graph.nodes:
                graphs[language] = scoped_graph
        if not graphs:
            raise ValueError(
                f"Cannot build clustering scope for component {component.component_id}: no owned CFG nodes"
            )

        remaining_depth = max(1, hierarchy_depth - _component_depth(component.component_id))
        scope = ClusteringService().build_scope_hierarchy(
            graphs,
            remaining_depth,
            component.component_id,
        )
        self.clustering_hierarchy.register_scope(component.component_id, scope)

    def _generate_subcomponents(
        self,
        analysis: AnalysisInsights,
        root_components: list[Component],
        existing_sub_analyses: dict[str, AnalysisInsights] | None = None,
    ) -> tuple[list[Component], dict[str, AnalysisInsights]]:
        """Generate subcomponents using absolute component depth and a frontier queue.

        ``existing_sub_analyses`` seeds the progress saves. A save with a non-None
        ``sub_analyses`` replaces the whole set on disk, so the incremental path — which
        only re-details the newly created components — must hand its live tree in or every
        intermediate save would publish an analysis.json with the untouched subtrees gone.
        """
        max_workers = min(os.cpu_count() or 4, 8)

        expanded_components: list[Component] = []
        sub_analyses: dict[str, AnalysisInsights] = dict(existing_sub_analyses or {})

        # Group stats to avoid cluttering the local variable scope
        stats = {"submitted": 0, "completed": 0, "saves": 0, "errors": 0}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task: dict[Future, tuple[Component, int]] = {}

            def submit_component(comp: Component, lvl: int):
                future = executor.submit(self._process_component, comp)
                future_to_task[future] = (comp, lvl)
                stats["submitted"] += 1
                logger.debug("Submitted component='%s' at level=%d", comp.name, lvl)

            # 1. Initial Seeding
            for component, level in _component_expansion_seeds(root_components, self.depth_level):
                submit_component(component, level)

            logger.info(
                "Subcomponent generation started with %d workers. Initial tasks: %d", max_workers, stats["submitted"]
            )

            # 2. Process Queue
            while future_to_task:
                completed_futures, _ = wait(future_to_task.keys(), return_when=FIRST_COMPLETED)

                for future in completed_futures:
                    component, level = future_to_task.pop(future)
                    stats["completed"] += 1

                    try:
                        comp_name, sub_analysis, new_components = future.result()

                        if comp_name and sub_analysis:
                            sub_analyses[comp_name] = sub_analysis
                            expanded_components.append(component)
                            stats["saves"] += 1

                            logger.debug("Saving intermediate analysis for '%s'", comp_name)
                            self._strip_ignored(analysis, sub_analyses)
                            expandable_ids, sub_expandable_ids = self._expandable_ids_for_tree(analysis, sub_analyses)
                            save_analysis(
                                analysis=analysis,
                                output_dir=Path(self.output_dir),
                                sub_analyses=sub_analyses,
                                repo_name=self.repo_name,
                                repo_dir=self.repo_location,
                                source_tree_hash=self._source_tree_hash(),
                                expandable_component_ids=expandable_ids,
                                sub_expandable_ids=sub_expandable_ids,
                                depth_cap=self.depth_level,
                            )

                        if new_components and level + 1 < self.depth_level:
                            for child in new_components:
                                submit_component(child, level + 1)

                            logger.info("Expanded '%s' with %d new children.", comp_name, len(new_components))

                    except LLMAuthError:
                        # Rejected key: abort the whole run rather than logging one
                        # error per component and continuing with a dead key.
                        raise
                    except Exception:
                        stats["errors"] += 1
                        logger.exception("Component '%s' generated an exception", component.name)

                logger.info(
                    "Progress: %d completed, %d in flight, %d errors",
                    stats["completed"],
                    len(future_to_task),
                    stats["errors"],
                )

            logger.info("Subcomponent generation complete: %s", stats)

        return expanded_components, sub_analyses

    @track_analysis
    def generate_analysis(self) -> Path:
        """
        Generate the graph analysis for the given repository.
        The output is stored in a single analysis.json file in output_dir.
        Components are analyzed in parallel as soon as their parents complete.
        """
        if (
            self.details_agent is None
            or self.abstraction_agent is None
            or self.static_analysis is None
            or self.clustering_hierarchy is None
        ):
            self.prepare_analysis()

        # Start monitoring (tracks start time)
        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Generate the initial analysis
            logger.info("Generating initial analysis")

            assert self.abstraction_agent is not None
            assert self.clustering_hierarchy is not None
            analysis, _cluster_results = self.abstraction_agent.run(self.clustering_hierarchy)
            root_components = [
                component
                for component in analysis.components
                if component.component_id in self.clustering_hierarchy.preclustered_scopes
            ]
            logger.info(f"Found {len(root_components)} components to analyze at level 1")

            # Process components using a frontier queue: submit children as soon as parent finishes.
            _expanded_components, sub_analyses = self._generate_subcomponents(analysis, root_components)

            analysis_path = self.finalize_and_save(analysis, sub_analyses)
            logger.info(f"Analysis complete. Written unified analysis to {analysis_path}")
            return analysis_path

    def rebuild_global_relations(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> list:
        """Rebuild cross-boundary component relations at the deepest available granularity.

        Walks the full CFG with a global node->deepest-component-id map so we
        catch edges like ``1.1.1 -> 2.1.2`` that per-level analysis cannot see.
        Mutates ``root_analysis.components_relations`` in place.
        """
        if not self.static_analysis:
            return []
        cfg_graphs = {str(lang): self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()}
        global_relations = build_global_relations(root_analysis, sub_analyses, cfg_graphs)
        if self._baseline_global_relations is not None:
            # Incremental: the wholesale rebuild would relabel edges between two untouched
            # components, so carry those over verbatim from the baseline.
            changed_ids = _incremental_changed_component_ids(
                root_analysis,
                sub_analyses,
                self._baseline_component_ids,
                self._baseline_member_keys,
                self._changed_members,
                self._changed_unattributed_files,
            )
            live_ids = {
                component.component_id
                for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses)
                for component in analysis.components
                if component.component_id
            }
            live_qnames = {
                qualified_name
                for lang in self.static_analysis.get_languages()
                for qualified_name in self.static_analysis.get_cfg(lang).nodes
            }
            global_relations = preserve_unchanged_relations(
                global_relations,
                self._baseline_global_relations,
                changed_ids,
                live_ids,
                live_qnames,
                self._changed_members,
            )
            # Same reason as the per-scope path: preservation re-injects baseline edges after
            # the grounding filters ran, so the assembled list is filtered once more.
            global_relations = prune_ungrounded_edges(
                global_relations,
                build_owner_index(build_global_node_to_component_map(root_analysis, sub_analyses)),
                StaticReferenceResolver(self.repo_location, self.static_analysis).keep_relation_edge,
                self._changed_members,
            )
        root_analysis.components_relations = global_relations
        return global_relations

    def finalize_for_save(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> None:
        """Prepare and validate an analysis tree for its authoritative save."""
        self._strip_ignored(root_analysis, sub_analyses)
        # Absorption must not erase the evidence of an invalid parent-child boundary.
        assert_scope_containment(root_analysis, sub_analyses)
        self.rebuild_global_relations(root_analysis, sub_analyses)
        cluster_caches = (
            [self.static_analysis.get_clusters(lang) for lang in self.static_analysis.get_languages()]
            if self.static_analysis
            else []
        )
        absorbed_ids = absorb_single_child_components(root_analysis, sub_analyses, cluster_caches)
        if hierarchy := getattr(self, "clustering_hierarchy", None):
            hierarchy.reroot_indexes(absorbed_ids)
        assert_scope_containment(root_analysis, sub_analyses)

    def finalize_and_save(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        *,
        seed_delta: dict[str, ClusterResult] | None = None,
        persist_side_artifacts: bool = True,
    ) -> Path:
        """Shared post-analysis tail for every flow: finalize, persist, return the path.

        ``finalize_for_save`` then ``save_analysis`` (stamped with the current
        ``source_tree_hash`` and file-coverage summary). ``seed_delta`` is the
        incremental-only cluster baseline, seeded *after* the save so a crash in
        between re-does the delta (idempotent) rather than silently skipping it.

        ``persist_side_artifacts`` writes ``file_coverage.json``, the static-
        analysis cache, and the ``fingerprint.json`` sidecar. The partial flow
        sets it False: it regenerates one component, not the source state, so
        rewriting those would drop the ``static_analysis.sha`` tag (cold-starting
        the next incremental) and desync the sidecar from ``source_tree_hash``.
        """
        self.finalize_for_save(root_analysis, sub_analyses)
        if persist_side_artifacts:
            source_tree_hash = self._source_tree_hash()
        else:
            # Partial: keep the prior hash so metadata matches the unrewritten sidecar.
            prior_metadata = load_analysis_metadata(Path(self.output_dir)) or {}
            source_tree_hash = prior_metadata.get("source_tree_hash", "") or self._source_tree_hash()
        expandable_component_ids, sub_expandable_ids = self._expandable_ids_for_tree(root_analysis, sub_analyses)
        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=Path(self.output_dir),
            sub_analyses=sub_analyses,
            repo_name=self.repo_name,
            file_coverage_summary=self._build_file_coverage_summary(),
            repo_dir=self.repo_location,
            source_tree_hash=source_tree_hash,
            expandable_component_ids=expandable_component_ids,
            sub_expandable_ids=sub_expandable_ids,
            depth_cap=self.depth_level,
        ).resolve()
        if seed_delta is not None:
            self._seed_incremental_cluster_cache(seed_delta)
        if persist_side_artifacts:
            self._write_file_coverage()
            self._persist_static_analysis_artifact()
            # Whole-tree sidecar (not the component-only files block) so the next
            # incremental diffs the same set source_tree_hash covers.
            write_fingerprint(Path(self.output_dir), self._source_tree_fingerprint_map())
        return analysis_path

    def _build_file_coverage_summary(self) -> FileCoverageSummary | None:
        if not self.file_coverage_data:
            return None
        summary = self.file_coverage_data["summary"]
        return FileCoverageSummary(
            total_files=summary["total_files"],
            analyzed=summary["analyzed"],
            not_analyzed=summary["not_analyzed"],
            not_analyzed_by_reason=summary["not_analyzed_by_reason"],
        )

    def _rescope_child_analyses(
        self,
        scope: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        preserved_ids: set[str],
    ) -> None:
        """Reconcile each child scope whose membership diverges from its parent's, surgically.

        Why: ``update_scope`` re-partitions a parent against the live clustering, but a
        child scope is a separate ``AnalysisInsights`` that no patch touches — a method
        that moved to another component would otherwise stay in the old owner's subtree
        and appear under two components.

        Only reconcile a scope whose parent's live method set differs from what its
        children currently reflect; an agreeing scope is left byte-for-byte, so a small
        change stops rippling into subtrees nothing touched. The reconcile itself is
        surgical (drop departed, graft entered) rather than a fresh re-cluster, so even a
        genuinely-changed component keeps its unchanged methods where they already were.
        Recurse into every scope so a deeper boundary that shifted is still caught.

        ``preserved_ids`` are components whose subtree was already restored verbatim from
        the baseline; reconciling them would graft the parent's undistributed methods into
        children and re-drift the very structure the restore froze, so skip them entirely.
        """
        for component in scope.components:
            if component.component_id in preserved_ids:
                continue
            child_scope = sub_analyses.get(component.component_id)
            if child_scope is None or not child_scope.components:
                continue
            parent_keys = _owned_method_keys([component])
            child_keys = _owned_method_keys(child_scope.components)
            if parent_keys != child_keys:
                _reconcile_child_scope(component, child_scope, parent_keys, child_keys, self.repo_location)
            self._rescope_child_analyses(child_scope, sub_analyses, preserved_ids)

    def _apply_incremental_scope_recursively(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, nx.DiGraph],
        sub_analyses: dict[str, AnalysisInsights],
        changed_members: ChangedMembers | None,
    ) -> RecursiveScopeUpdateResult:
        assert self.incremental_agent is not None
        # Structure is derived, not asked for — see diagram_analysis/scope_plan.py.
        decision = plan_scope_update(
            scope_id, scope, cluster_results, cfg_graphs, self._changed_members, self.repo_location
        )
        clustering = ClusterScopeResult(scope_id=scope_id, leaf_clusters_by_language=cluster_results)
        apply_result = self.incremental_agent.update_scope(scope_id, scope, decision, clustering)
        result = RecursiveScopeUpdateResult(
            refresh_ids=set(apply_result.refresh_ids),
            new_component_ids=set(apply_result.new_component_ids),
            removed_ids=set(apply_result.removed_ids),
        )
        if apply_result.refresh_ids or apply_result.removed_ids:
            result.relation_contexts[scope_id] = apply_result.relation_context

        components_by_id = {
            component.component_id: component for component in scope.components if component.component_id
        }
        existing_refresh_ids = apply_result.refresh_ids - apply_result.new_component_ids
        for component_id in sorted(existing_refresh_ids):
            child_scope = sub_analyses.get(component_id)
            child_component = components_by_id.get(component_id)
            if child_scope is None or child_component is None or _component_depth(component_id) >= self.depth_level:
                continue
            child_cluster_results, child_cfgs, child_diff = _build_scope_incremental_inputs(
                child_component,
                component_id,
                self.incremental_agent,
                self.changes,
                self.repo_location,
                changed_members,
            )
            if not child_diff.has_changes:
                continue
            if not _child_scope_needs_recursive_update(child_scope, child_diff):
                continue
            child_result = self._apply_incremental_scope_recursively(
                component_id,
                child_scope,
                child_cluster_results,
                child_cfgs,
                sub_analyses,
                changed_members,
            )
            result.refresh_ids |= child_result.refresh_ids
            result.new_component_ids |= child_result.new_component_ids
            result.removed_ids |= child_result.removed_ids
            result.relation_contexts.update(child_result.relation_contexts)
        return result

    def _apply_incremental_hierarchy(
        self,
        clustering: ClusterScopeResult,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> RecursiveScopeUpdateResult:
        """Apply persisted-scope updates by traversing one precomputed hierarchy."""
        assert self.incremental_agent is not None
        scope = root_analysis if clustering.scope_id == ROOT_SCOPE_ID else sub_analyses.get(clustering.scope_id)
        if scope is None:
            return RecursiveScopeUpdateResult()

        decision = plan_scope_result_update(scope, clustering, self._changed_members)
        applied = self.incremental_agent.update_scope(
            clustering.scope_id,
            scope,
            decision,
            clustering,
        )
        result = RecursiveScopeUpdateResult(
            refresh_ids=set(applied.refresh_ids),
            new_component_ids=set(applied.new_component_ids),
            removed_ids=set(applied.removed_ids),
        )
        if applied.refresh_ids or applied.removed_ids:
            result.relation_contexts[clustering.scope_id] = applied.relation_context

        for group in clustering.groups:
            if group.children is None or group.group_id not in sub_analyses:
                continue
            child_result = self._apply_incremental_hierarchy(group.children, root_analysis, sub_analyses)
            result.refresh_ids |= child_result.refresh_ids
            result.new_component_ids |= child_result.new_component_ids
            result.removed_ids |= child_result.removed_ids
            result.relation_contexts.update(child_result.relation_contexts)
        return result

    @track_analysis
    def generate_analysis_incremental(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> Path:
        """Update an existing analysis from one upfront, recursively anchored hierarchy."""
        if self.details_agent is None or self.incremental_agent is None:
            self.prepare_analysis(require_incremental_baseline=True)
        assert self.static_analysis is not None
        assert self.details_agent is not None
        assert self.incremental_agent is not None

        # Snapshot the loaded baseline before any mutation: its global relations (deepest
        # granularity, keyed by component id) are carried over verbatim at save time for any
        # edge between two components that did not change. This is what marks the run as
        # incremental for ``rebuild_global_relations``; a full run leaves it ``None``.
        self._baseline_component_ids = {
            component.component_id
            for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses)
            for component in analysis.components
            if component.component_id
        }
        self._baseline_global_relations = {
            (relation.src_id, relation.dst_id): relation.model_copy(deep=True)
            for relation in root_analysis.components_relations
            if relation.src_id and relation.dst_id
        }
        # Capture per-component member keys BEFORE remove_deleted_files scrubs ownership: a deleted
        # method must still register as a membership change so its component isn't treated as
        # unchanged and its stale baseline relations restored. Kept separate from the full
        # membership baseline (captured post-scrub below) so the restore passes never re-inject a
        # deleted method. Also drives the empty-delta path, which returns before that capture.
        self._baseline_member_keys = _capture_baseline_member_keys(root_analysis, sub_analyses)
        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Scrub before cluster math: orphan-routed files never appear in
            # any cluster, so deletes wouldn't surface via the delta alone.
            live_files: set[str] = set()
            for language in self.static_analysis.get_languages():
                try:
                    cfg = self.static_analysis.get_cfg(language)
                except (ValueError, KeyError):
                    continue
                for node in cfg.nodes.values():
                    if node.file_path:
                        live_files.add(normalize_repo_path(node.file_path, self.repo_location))
            remove_deleted_files(root_analysis, sub_analyses, live_files)

            # Member-granular change signal from per-method content hashes,
            # captured from the baseline analysis.json *before* the files index
            # is refreshed (which would overwrite the prior hashes). Drives the
            # modified decision so a body-only edit lights up only the clusters
            # whose own members changed, not every cluster sharing the file.
            changed_members = (
                compute_changed_members(
                    root_analysis.files,
                    self.static_analysis,
                    self.changes,
                    self.repo_location,
                )
                if self.changes is not None
                else None
            )
            # Body-changed qnames drive copy-forward and the save-time relation preservation.
            self._changed_members = changed_members.members if changed_members is not None else set()
            # Module-level edits no member represents dirty the owning component too.
            self._changed_unattributed_files = (
                changed_members.unattributed_files if changed_members is not None else set()
            )

            snapshot_source = self.static_analysis.incremental_base_results
            if snapshot_source is None:
                error = IncrementalCacheMissingError(self.output_dir)
                logger.error("%s", error)
                raise error
            old_snapshot = snapshot_from_static_analysis(snapshot_source)
            if not old_snapshot.all_cluster_ids():
                # No cluster_cache on the live CFG — no prior pkl, legacy pkl,
                # or first-ever incremental run. Refuse to silently rebuild
                # from scratch; that would discard the existing analysis.json's
                # depth and component IDs. Caller must explicitly request a
                # full run instead.  ``IncrementalCacheMissingError`` inspects
                # the artifact dir to pick the specific diagnostic (missing
                # pkl, missing sha, or pkl-without-cluster-baseline).
                artifact_dir = self.output_dir
                error = IncrementalCacheMissingError(artifact_dir)
                logger.error("%s", error)
                raise error

            delta = compute_cluster_delta(
                old_snapshot,
                self.static_analysis,
                changes=self.changes,
                repo_dir=self.repo_location,
            )
            if not delta.has_changes:
                logger.info("Cluster delta is empty; rewriting current analysis without re-detailing.")
                # No structural change, but a body-only edit still moves content
                # hashes — refresh the files index from live source so they don't
                # go stale (relations are already the global set here).
                # Re-scope anyway: a baseline written before child scopes were
                # confined to their parent stays drifted until something repairs it.
                self._rescope_child_analyses(root_analysis, sub_analyses, set())
                self._refresh_files_index(root_analysis, sub_analyses)
                return self.finalize_and_save(root_analysis, sub_analyses)

            # Full membership baseline for the restore/rescope passes, captured AFTER the deletion
            # scrub so a deleted method is never re-injected from the baseline into a live scope.
            baseline_membership = _capture_membership_baseline(root_analysis, sub_analyses)
            root_cluster_results = delta.cluster_results()
            changed_files = (
                {
                    normalize_repo_path(change.file_path, self.repo_location)
                    for change in self.changes.files
                    if change.is_content_change()
                }
                if self.changes is not None
                else set()
            )
            hierarchy = ClusteringService().build_incremental_hierarchy(
                self.static_analysis,
                self.depth_level,
                root_cluster_results,
                {ROOT_SCOPE_ID: root_analysis, **sub_analyses},
                self._changed_members,
                changed_files,
                self.repo_location,
                self.output_dir,
            )
            self.clustering_hierarchy = hierarchy
            apply_result = self._apply_incremental_hierarchy(hierarchy, root_analysis, sub_analyses)
            # Freeze the whole subtree of components with no changed member, then reconcile
            # child scopes whose repaired parent membership genuinely changed.
            preserved_ids = _restore_unchanged_subtrees(
                root_analysis,
                sub_analyses,
                baseline_membership,
                self._changed_members,
                self._changed_unattributed_files,
                apply_result.new_component_ids,
            )
            self._rescope_child_analyses(root_analysis, sub_analyses, preserved_ids)
            # Fail before the first write rather than after: the later save-time check would
            # leave a persisted tree from the intermediate saves below.
            assert_scope_containment(root_analysis, sub_analyses)
            # A component identical to its baseline did not change: restore any metadata the
            # planner reworded and drop it from the refresh set so its relations carry over.
            unchanged_ids = _restore_unchanged_metadata(
                root_analysis,
                sub_analyses,
                baseline_membership,
                self._changed_members,
                self._changed_unattributed_files,
            )
            apply_result.refresh_ids -= unchanged_ids

            removed_ids = prune_empty_components(root_analysis, sub_analyses)
            if removed_ids:
                apply_result.refresh_ids -= removed_ids
                apply_result.new_component_ids -= removed_ids
            _drop_removed_subtree_analyses(sub_analyses, apply_result.removed_ids | removed_ids)

            created_components = _collect_components_by_id(
                apply_result.new_component_ids,
                root_analysis,
                sub_analyses,
            )
            if created_components:
                self.incremental_agent.detail_new_components(created_components)

            new_components = [
                component
                for component in created_components
                if component.component_id in hierarchy.preclustered_scopes
                and _component_depth(component.component_id) < self.depth_level
            ]
            if new_components:
                _, redetailed_subs = self._generate_subcomponents(root_analysis, new_components, sub_analyses)
                _merge_sub_analyses(sub_analyses, redetailed_subs)

            if apply_result.relation_contexts:
                # Each context froze its changed set when its scope was planned, before the
                # copy-forward pass proved some of those components byte-identical and dropped
                # them from the refresh set. Narrow the contexts to what actually changed, or
                # relations between two restored components would be reworded for nothing.
                settled = apply_result.refresh_ids | apply_result.new_component_ids | apply_result.removed_ids
                self.incremental_agent.generate_all_scope_relations(
                    root_analysis,
                    sub_analyses,
                    {
                        scope_id: replace(context, changed_ids=context.changed_ids & settled)
                        for scope_id, context in apply_result.relation_contexts.items()
                    },
                    self._changed_members,
                )

            self._refresh_files_index(root_analysis, sub_analyses)

            analysis_path = self.finalize_and_save(root_analysis, sub_analyses, seed_delta=delta.cluster_results())
            n_subs = sum(len(sub.components) for sub in sub_analyses.values())
            logger.info(
                "[incremental] saved: %d root + %d sub-components, %d relations",
                len(root_analysis.components),
                n_subs,
                len(root_analysis.components_relations),
            )
            return analysis_path

    def _refresh_files_index(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> None:
        """Rebuild live per-scope file indexes and union them into the root index."""
        assert self.static_analysis is not None
        analyses = (root_analysis, *sub_analyses.values())
        source_cache: SourceCache = {}
        for analysis in analyses:
            refresh_method_spans_from_cfg(analysis, self.static_analysis, self.repo_location)
            analysis.files = build_files_index(analysis, self.repo_location, source_cache)
            index_relation_endpoints(analysis, self.repo_location)

        unified_files: dict[str, FileEntry] = {}
        for analysis in analyses:
            for fp, entry in analysis.files.items():
                unified_files.setdefault(fp, FileEntry()).merge_from(entry)
        root_analysis.files = unified_files


def assert_scope_containment(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> None:
    """Raise ``ScopeContainmentError`` if any child scope owns methods its parent does not."""
    components_by_id = {
        component.component_id: component
        for analysis in [root_analysis, *sub_analyses.values()]
        for component in analysis.components
        if component.component_id
    }
    violations: list[str] = []
    for component_id, child_scope in sorted(sub_analyses.items()):
        parent = components_by_id.get(component_id)
        if parent is None:
            continue
        owned = _member_keys(parent)
        for child in child_scope.components:
            escaped = _member_keys(child) - owned
            if escaped:
                violations.append(
                    f"{child.component_id or child.name} holds {len(escaped)} method(s) outside parent {component_id}"
                )
    if violations:
        raise ScopeContainmentError(violations)


def _collect_components_by_id(
    component_ids: set[str],
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> list[Component]:
    """Return concrete ``Component`` objects matching the given IDs across root + sub-analyses."""
    if not component_ids:
        return []
    found: list[Component] = []
    seen: set[str] = set()
    for analysis in [root_analysis, *sub_analyses.values()]:
        for component in analysis.components:
            if component.component_id in component_ids and component.component_id not in seen:
                found.append(component)
                seen.add(component.component_id)
    return found


def _drop_removed_subtree_analyses(sub_analyses: dict[str, AnalysisInsights], removed_ids: set[str]) -> None:
    for removed_id in removed_ids:
        for scope_id in list(sub_analyses):
            if is_self_or_descendant(scope_id, removed_id):
                del sub_analyses[scope_id]


def _child_scope_needs_recursive_update(
    child_scope: AnalysisInsights,
    structural_diff: StructuralClusterDiff,
) -> bool:
    owned_qnames = {
        method.qualified_name
        for component in child_scope.components
        for group in component.file_methods
        for method in group.methods
        if method.qualified_name
    }
    # A module-level edit no member represents surfaces only as dirty_files, so match on the
    # child's owned files too — otherwise a pure import/constant edit in a file this expanded
    # child owns refreshes the parent but leaves the child's descriptions/relations stale.
    owned_files = {
        normalize_repo_path(group.file_path)
        for component in child_scope.components
        for group in component.file_methods
        if group.file_path
    }
    changed_qnames: set[str] = set()
    dirty_files: set[str] = set()
    for diff in structural_diff.by_language.values():
        for member_delta in [*diff.modified, *diff.new_details]:
            changed_qnames.update(member_delta.removed_methods, member_delta.added_methods, member_delta.dirty_members)
            dirty_files.update(normalize_repo_path(path) for path in member_delta.dirty_files)
    return bool(changed_qnames & owned_qnames) or bool(dirty_files & owned_files)


def _build_scope_incremental_inputs(
    component: Component,
    scope_id: str,
    incremental_agent: IncrementalAgent,
    changes: ChangeSet | None,
    repo_dir: Path,
    changed_members: ChangedMembers | None,
) -> tuple[dict[str, ClusterResult], dict[str, nx.DiGraph], StructuralClusterDiff]:
    old_snapshot = scoped_snapshot_for_component(component, scope_id, incremental_agent)
    if not old_snapshot.all_cluster_ids():
        return {}, {}, StructuralClusterDiff()

    cluster_results, subgraph_cfgs = incremental_agent._create_strict_component_subgraph(
        component,
        source_cluster_id_prefix=scope_id,
    )
    delta = ClusterDelta(
        by_language={
            language: LanguageDelta(language=language, cluster_results=cluster_result)
            for language, cluster_result in cluster_results.items()
        }
    )
    structural_diff = structural_diff_from_delta(
        old_snapshot,
        delta,
        changes=changes,
        repo_dir=repo_dir,
        scope_id=scope_id,
        changed=changed_members,
    )
    return (
        cluster_results,
        {lang: cfg.to_networkx(DEFAULT_REFERENCE_KINDS) for lang, cfg in subgraph_cfgs.items()},
        structural_diff,
    )


def scoped_snapshot_for_component(
    component: Component,
    scope_id: str,
    incremental_agent: IncrementalAgent,
) -> ClusterSnapshot:
    assigned_qnames = {
        method.qualified_name for group in component.file_methods for method in group.methods if method.qualified_name
    }
    by_language = {}
    for language in incremental_agent.static_analysis.get_languages():
        cfg = incremental_agent.static_analysis.get_cfg(language)
        sub_cfg = cfg.filter_by_nodes(assigned_qnames)
        if sub_cfg.nodes:
            method_paths = incremental_agent.static_analysis.get_clusters(language).method_paths
            by_language[str(language)] = ClusteringService._scoped_snapshot(
                sub_cfg,
                method_paths,
                scope_id,
            )
    return ClusterSnapshot(by_language=by_language)


def _merge_sub_analyses(
    target: dict[str, AnalysisInsights],
    updates: dict[str, AnalysisInsights],
) -> None:
    """Merge *updates* into *target*, preserving components the redetailer didn't touch.

    ``_generate_subcomponents`` produces fresh sub-analyses that only contain
    components the detailer LLM generated. In the incremental path, scoped
    operations may have inserted brand-new components that the detailer never
    saw because they weren't in its input scope. A plain ``dict.update()``
    would wipe those survivors out.

    For each key in *updates*, we:
      1. Keep old components whose IDs are absent from the new sub-analysis.
      2. Replace everything else with the new sub-analysis data.

    Relations are not merged here: they live once on the root as the global set
    and are rebuilt wholesale by ``rebuild_global_relations`` after this merge.
    """
    for key, new_sub in updates.items():
        old_sub = target.get(key)
        if old_sub is None:
            target[key] = new_sub
            continue

        new_ids = {c.component_id for c in new_sub.components}
        surviving = [c for c in old_sub.components if c.component_id not in new_ids]
        if surviving:
            new_sub.components = surviving + new_sub.components

        target[key] = new_sub
