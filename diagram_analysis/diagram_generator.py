import json
import logging
import os
import time
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from langchain_core.language_models import BaseChatModel

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    SourceCodeReference,
)
from agents.component_ownership import ComponentOwnershipIndex
from diagram_analysis.incremental_update import (
    IncrementalUpdater,
    prune_empty_components,
    remove_deleted_files,
)
from agents.incremental_results import RecursiveScopeUpdateResult
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from agents.llm_config import initialize_agent_llm, initialize_llms
from agents.relation_edges import (
    drop_misattributed_edges,
    index_relation_endpoints,
    preserve_unchanged_relations,
    prune_ungrounded_edges,
)
from agents.scope_ids import ROOT_SCOPE_ID
from agents.scope_analysis_agent import ScopeAnalysisAgent
from agents.content_hash import SourceCache, hash_repo_source_files, tree_hash_from_file_hashes
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.exceptions import (
    ClusteringScopeUnavailableError,
    ScopeContainmentError,
)
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.file_index import build_files_index, refresh_method_spans_from_cfg
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis, write_fingerprint
from diagram_analysis.incremental_changes import compute_changed_members
from diagram_analysis.scope_assembly import ScopeAssembler
from repo_utils.path_utils import normalize_repo_path
from diagram_analysis.scope_plan import plan_scope_result_update
from diagram_analysis.tree_shape import absorb_single_child_components
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import get_monitoring_run_dir
from repo_utils.change_detector import ChangeSet
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer, get_static_analysis
from static_analyzer.cfg import CallGraph
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolver import StaticReferenceResolver
from static_analyzer.cluster_relations import (
    build_global_node_to_component_map,
    build_global_relations,
    is_self_or_descendant,
)
from static_analyzer.config import Language
from static_analyzer.clustering import (
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
)
from static_analyzer.clustering.exceptions import IncrementalCacheMissingError, PlannerUnavailableError
from static_analyzer.clustering.names import AffinityGrouper, Grouper, KinshipGrouper, TreeSpec
from static_analyzer.clustering.names.spec import SPEC_VERSION
from static_analyzer.clustering.service import ClusteringService, hierarchy_differs
from agents.tree_planner_agent import TreePlannerAgent
from user_config import GROUPER_ENV, GROUPERS
from static_analyzer.scanner import ProjectScanner
from telemetry.events import track_analysis

logger = logging.getLogger(__name__)

_EMPTY_PERSISTED_SCOPES: Mapping[str, AnalysisInsights] = MappingProxyType({})


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


@dataclass
class _IncrementalPreparation:
    """Clustering inputs prepared before incremental updates run."""

    structure_changed: bool
    baseline_membership: _MembershipBaseline
    has_membership_changes: bool = False
    has_source_changes: bool = False

    @property
    def has_changes(self) -> bool:
        return self.structure_changed or self.has_membership_changes or self.has_source_changes


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
        # Source-tree changeset for the iterative path: the files the warm-start re-LSPs and
        # the members whose bodies changed. ``None`` is a full run.
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

        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.clustering_hierarchy: ClusterScopeResult | None = None
        # The tree specification the components are drawn from: drafted by a full analysis,
        # read back by every run that builds on one, persisted with every save.
        self.tree_spec: TreeSpec | None = None
        self._llms: tuple[BaseChatModel, BaseChatModel] | None = None
        self._incremental_preparation: _IncrementalPreparation | None = None
        self.scope_assembler = ScopeAssembler(repo_location)
        self.scope_analysis_agent: ScopeAnalysisAgent | None = None
        self.incremental_updater: IncrementalUpdater | None = None
        self.file_coverage_data: dict | None = None

        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None
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
        incremental: bool = False,
        persisted_scopes: Mapping[str, AnalysisInsights] = _EMPTY_PERSISTED_SCOPES,
    ) -> None:
        """Run source fingerprinting, static analysis, and deterministic clustering."""
        if incremental and target_component is not None:
            raise ValueError("Incremental clustering does not support a selected component scope")
        self._incremental_preparation = None
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
        depth = hierarchy_depth if hierarchy_depth is not None else self.depth_level
        if incremental:
            root_analysis = persisted_scopes.get(ROOT_SCOPE_ID)
            if root_analysis is None:
                raise ValueError("Incremental clustering requires the persisted root analysis")
            sub_analyses = {
                scope_id: analysis for scope_id, analysis in persisted_scopes.items() if scope_id != ROOT_SCOPE_ID
            }
            self.tree_spec = self._stored_tree_spec()
            self._incremental_preparation = self._prepare_incremental_clustering(root_analysis, sub_analyses, depth)
        elif target_component is None:
            service = ClusteringService(self._grouper())
            self.clustering_hierarchy = service.build_full_hierarchy(static_analysis, depth)
            self.tree_spec = service.spec
        else:
            # A partial run expands one component of an existing analysis, so it replays the
            # specification that analysis was drawn from rather than drafting a new one.
            self.tree_spec = self._stored_tree_spec()
            scope = self._build_component_scope(target_component, depth)
            self.clustering_hierarchy = ClusterScopeResult(scope_id=ROOT_SCOPE_ID)
            self.clustering_hierarchy.register_scope(target_component.component_id, scope)

        # --- Capture Static Analysis Stats ---
        static_stats: dict[str, Any] = {"repo_name": self.repo_name, "languages": {}}
        scanner = ProjectScanner(self.repo_location)
        loc_by_language = {pl.language: pl.size for pl in scanner.scan()}
        for language in sorted(static_analysis.present_languages(), key=str):
            files = static_analysis.source_files_of_language(language)
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

    def _stored_tree_spec(self) -> TreeSpec:
        """The specification the baseline analysis.json was drawn from."""
        stored = (load_analysis_metadata(Path(self.output_dir)) or {}).get("tree_spec")
        if not stored:
            raise IncrementalCacheMissingError(
                Path(self.output_dir),
                "the baseline analysis.json carries no tree specification (written before it existed)",
            )
        if stored.get("version") != SPEC_VERSION:
            raise IncrementalCacheMissingError(
                Path(self.output_dir),
                f"the baseline tree specification is version {stored.get('version')}; this build replays {SPEC_VERSION}",
            )
        return TreeSpec.from_dict(stored)

    def _grouper(self) -> Grouper:
        """The configured grouper: affinity unless another was asked for; the planner needs an LLM."""
        choice = os.getenv(GROUPER_ENV, GROUPERS[0])
        if choice not in GROUPERS:
            raise ValueError(f"{GROUPER_ENV} must be one of {', '.join(GROUPERS)}, not {choice!r}")
        if choice == "affinity":
            return AffinityGrouper()
        if choice == "kinship":
            return KinshipGrouper()
        if self._llms is None:
            self._llms = initialize_llms()
        if self.static_analysis is None:
            raise PlannerUnavailableError("no LLM was initialised before clustering")
        agent_llm, parsing_llm = self._llms
        planner = TreePlannerAgent(self.repo_location, self.static_analysis, agent_llm, parsing_llm)
        self._monitoring_agents["TreePlannerAgent"] = planner
        return planner

    def _scope_agent(self) -> ScopeAnalysisAgent:
        """Initialize the shared semantic agent only when a scope needs analysis."""
        if self.scope_analysis_agent is None:
            assert self.static_analysis is not None
            agent_llm = self._llms[0] if self._llms is not None else initialize_agent_llm()
            self.scope_analysis_agent = ScopeAnalysisAgent(self.repo_location, self.static_analysis, agent_llm)
            self._monitoring_agents["ScopeAnalysisAgent"] = self.scope_analysis_agent
        return self.scope_analysis_agent

    def _enrich_scope(
        self,
        scope: ClusterScopeResult,
        analysis: AnalysisInsights,
        editable_group_ids: set[str],
        locked_name_ids: frozenset[str] = frozenset(),
        changed_files: frozenset[str] = frozenset(),
        incremental: bool = False,
    ) -> None:
        """Apply bounded semantic enrichment while preserving deterministic scope structure."""
        if not editable_group_ids:
            return
        try:
            semantics = self._scope_agent().analyze(
                scope,
                analysis,
                editable_group_ids,
                locked_name_ids,
                changed_files,
                incremental,
            )
        except Exception:
            logger.exception("Semantic analysis failed for scope %s; retaining deterministic output", scope.scope_id)
            return
        if semantics is None:
            return
        assert self.static_analysis is not None
        self.scope_assembler.apply_semantics(
            analysis,
            scope,
            semantics,
            editable_group_ids,
            set(locked_name_ids),
            StaticReferenceResolver(self.repo_location, self.static_analysis),
        )

    def _enrich_incremental_scopes(
        self,
        hierarchy: ClusterScopeResult,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        refreshed_ids: set[str],
        baseline_membership: _MembershipBaseline,
        skip_scope_ids: frozenset[str] = frozenset(),
    ) -> None:
        """Re-run semantic analysis only for scopes containing changed groups or files."""
        changed_files = self._incremental_changed_files(root_analysis)
        for scope in self._cluster_scopes(hierarchy):
            if scope.scope_id in skip_scope_ids:
                continue
            analysis = root_analysis if scope.scope_id == ROOT_SCOPE_ID else sub_analyses.get(scope.scope_id)
            if analysis is None:
                continue
            editable_ids = {
                group.group_id
                for group in scope.groups
                if group.group_id in refreshed_ids
                or bool(self._group_files(group, scope) & changed_files)
                or bool(group.qualified_names & self._changed_members)
            }
            if not editable_ids:
                continue
            locked_ids = frozenset(
                group.group_id
                for group in scope.groups
                if group.group_id in editable_ids
                and group.group_id in baseline_membership.meta_by_id
                and self._group_member_keys(group, scope) == baseline_membership.meta_by_id[group.group_id].member_keys
            )
            self._enrich_scope(
                scope,
                analysis,
                editable_ids,
                locked_ids,
                frozenset(changed_files),
                incremental=True,
            )

    def _incremental_changed_files(self, root_analysis: AnalysisInsights) -> set[str]:
        """Map changed live and removed members back to repository-relative files."""
        changed_files = set(self._changed_unattributed_files)
        for graph in self.static_analysis.available_cfgs().values() if self.static_analysis is not None else ():
            for qualified_name in self._changed_members:
                node = graph.nodes.get(qualified_name)
                if node is not None and node.file_path:
                    changed_files.add(normalize_repo_path(node.file_path, self.repo_location))
        for file_path, entry in root_analysis.files.items():
            if any(method.qualified_name in self._changed_members for method in entry.methods):
                changed_files.add(normalize_repo_path(file_path, self.repo_location))
        return changed_files

    def _group_files(self, group: ClusterGroup, scope: ClusterScopeResult) -> set[str]:
        """Return normalized files containing a deterministic group's symbols."""
        files: set[str] = set()
        for language, qualified_names in group.symbol_members_by_language.items():
            graph = scope.graphs_by_language.get(language)
            if graph is None:
                continue
            for qualified_name in qualified_names:
                node = graph.nodes.get(qualified_name)
                if node is not None and node.file_path:
                    files.add(normalize_repo_path(node.file_path, self.repo_location))
        return files

    def _group_member_keys(self, group: ClusterGroup, scope: ClusterScopeResult) -> frozenset[tuple[str, str]]:
        """Return current file and qualified-name keys for one group."""
        keys: set[tuple[str, str]] = set()
        for language, qualified_names in group.symbol_members_by_language.items():
            graph = scope.graphs_by_language.get(language)
            if graph is None:
                continue
            for qualified_name in qualified_names:
                node = graph.nodes.get(qualified_name)
                if node is not None and node.file_path:
                    keys.add((normalize_repo_path(node.file_path, self.repo_location), qualified_name))
        return frozenset(keys)

    @staticmethod
    def _cluster_scopes(hierarchy: ClusterScopeResult) -> list[ClusterScopeResult]:
        """Return every deterministic scope in hierarchy order."""
        scopes = [hierarchy]
        for group in hierarchy.groups:
            if group.children is not None:
                scopes.extend(DiagramGenerator._cluster_scopes(group.children))
        return scopes

    def _tree_spec_dict(self) -> dict | None:
        return self.tree_spec.to_dict() if self.tree_spec is not None else None

    def agent_init(self) -> None:
        """Initialize analysis helpers after deterministic analysis."""
        assert self.static_analysis is not None
        self.incremental_updater = IncrementalUpdater(self.repo_location, self.static_analysis, self.changes)
        self._initialize_stats_writer()

    def prepare_analysis(
        self,
        *,
        hierarchy_depth: int | None = None,
        target_component: Component | None = None,
        incremental: bool = False,
        persisted_scopes: Mapping[str, AnalysisInsights] = _EMPTY_PERSISTED_SCOPES,
    ) -> None:
        """Prepare deterministic inputs, then initialize analysis helpers."""
        self.deterministic_analysis(
            hierarchy_depth=hierarchy_depth,
            target_component=target_component,
            incremental=incremental,
            persisted_scopes=persisted_scopes,
        )
        self.agent_init()

    def _initialize_stats_writer(self) -> None:
        """Initialize monitoring after every active agent is known."""
        if self.monitoring_enabled:
            monitoring_dir = get_monitoring_run_dir(self.log_path, create=True)
            self.stats_writer = StreamingStatsWriter(
                monitoring_dir=monitoring_dir,
                agents_dict=self._monitoring_agents,
                repo_name=self.project_name or self.repo_name,
                output_dir=str(self.output_dir),
                start_time=self._analysis_start_time,
            )

    def _prepare_incremental_clustering(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        hierarchy_depth: int,
    ) -> _IncrementalPreparation:
        """Replay the stored specification and capture the incremental change context."""
        assert self.static_analysis is not None
        if self.static_analysis.incremental_base_results is None:
            error = IncrementalCacheMissingError(self.output_dir)
            logger.error("%s", error)
            raise error

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
        self._baseline_member_keys = _capture_baseline_member_keys(root_analysis, sub_analyses)

        live_files = {
            normalize_repo_path(node.file_path, self.repo_location)
            for graph in self.static_analysis.available_cfgs().values()
            for node in graph.nodes.values()
            if node.file_path
        }
        remove_deleted_files(root_analysis, sub_analyses, live_files)

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
        self._changed_members = changed_members.members if changed_members is not None else set()
        self._changed_unattributed_files = changed_members.unattributed_files if changed_members is not None else set()

        baseline_membership = _capture_membership_baseline(root_analysis, sub_analyses)
        assert self.tree_spec is not None
        persisted = {ROOT_SCOPE_ID: root_analysis, **sub_analyses}
        # Scopes the specification never reached are drafted deterministically here.
        service = ClusteringService()
        self.clustering_hierarchy = service.build_incremental_hierarchy(
            self.static_analysis,
            hierarchy_depth,
            self.tree_spec,
            persisted,
            self.repo_location,
            self.output_dir,
        )
        self.tree_spec = service.spec
        return _IncrementalPreparation(
            structure_changed=hierarchy_differs(self.clustering_hierarchy, persisted),
            baseline_membership=baseline_membership,
            has_membership_changes=changed_members.has_membership_changes if changed_members is not None else False,
            has_source_changes=bool(self._changed_members or self._changed_unattributed_files),
        )

    def _expandable_ids_for_tree(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        preserved_expandable_ids: Collection[str] = (),
    ) -> tuple[list[str] | None, dict[str, list[str]] | None]:
        """Return the clustering service's expansion decisions for every persisted scope."""
        clustering_groups = self.clustering_hierarchy.clustering_groups if self.clustering_hierarchy else {}
        if clustering_groups:

            def is_expandable(component: Component) -> bool:
                component_id = component.component_id
                if component_id in sub_analyses:
                    return True
                if not component.file_methods:
                    return False
                group = clustering_groups.get(component_id)
                if group is not None:
                    return group.expandable
                return component_id in preserved_expandable_ids

            def precomputed_ids(scope: AnalysisInsights) -> list[str]:
                return [
                    component.component_id
                    for component in scope.components
                    if component.component_id and is_expandable(component)
                ]

            return precomputed_ids(root_analysis), {
                scope_id: precomputed_ids(scope) for scope_id, scope in sub_analyses.items()
            }

        return None, None

    def _process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        """Analyze a component from its precomputed hierarchy scope."""
        preclustered_scopes = self.clustering_hierarchy.preclustered_scopes if self.clustering_hierarchy else {}
        scope = preclustered_scopes.get(component.component_id)
        if scope is None:
            raise ClusteringScopeUnavailableError(component.component_id, "no precomputed scope")

        try:
            analysis = self.scope_assembler.build(scope)
            self.scope_assembler.qualify_source_cluster_ids(analysis, component.component_id)
            self._enrich_scope(scope, analysis, {group.group_id for group in scope.groups})
            new_components = [child for child in analysis.components if child.component_id in preclustered_scopes]

            return component.component_id, analysis, new_components
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
        of which discovery path (LSP imports, clustering, plugin) added
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

    def _persist_static_analysis_artifact(self) -> None:
        """Persist the static-analysis artifact."""
        if self.static_analysis is None:
            return
        if self.source_sha is None:
            raise RuntimeError("Cannot persist static analysis without a source SHA")
        StaticAnalysisCache(self.output_dir, self.repo_location).save(self.static_analysis, source_sha=self.source_sha)

    def _source_tree_fingerprint_map(self) -> dict[str, str]:
        """The whole-tree fingerprint, fingerprinting on first use if preparation didn't."""
        if self._source_tree_fingerprint is None:
            self._source_tree_fingerprint = hash_repo_source_files(self.repo_location)
        return self._source_tree_fingerprint

    def _source_tree_hash(self) -> str:
        """The source-tree version key aggregated from the cached fingerprint."""
        return tree_hash_from_file_hashes(self._source_tree_fingerprint_map())

    def _build_component_scope(self, component: Component, hierarchy_depth: int) -> ClusterScopeResult:
        """Precompute an exact hierarchy rooted at one persisted component ID."""
        assert self.static_analysis is not None
        graphs = self.static_analysis.available_cfgs()
        if not graphs:
            raise ClusteringScopeUnavailableError(component.component_id, "no owned CFG nodes")
        remaining_depth = max(1, hierarchy_depth - _component_depth(component.component_id))
        assert self.tree_spec is not None
        service = ClusteringService(self._grouper())
        # The service replays the specification from the root down to this component, so the
        # scope holds the units a full run placed in it, data-only files included.
        scope = service.build_scope_hierarchy(graphs, remaining_depth, component.component_id, self.tree_spec)
        self.tree_spec = service.spec
        if not scope.groups:
            decided = self.tree_spec.scope(component.component_id)
            raise ClusteringScopeUnavailableError(
                component.component_id, decided.leaf_reason if decided is not None else "no rules"
            )
        return scope

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
                                tree_spec=self._tree_spec_dict(),
                            )

                        if new_components and level + 1 < self.depth_level:
                            for child in new_components:
                                submit_component(child, level + 1)

                            logger.info("Expanded '%s' with %d new children.", comp_name, len(new_components))

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
        if self.static_analysis is None or self.clustering_hierarchy is None:
            self.prepare_analysis()

        # Start monitoring (tracks start time)
        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Generate the initial analysis
            logger.info("Generating initial analysis")

            assert self.clustering_hierarchy is not None
            analysis = self.scope_assembler.build(self.clustering_hierarchy)
            self._enrich_scope(
                self.clustering_hierarchy,
                analysis,
                {group.group_id for group in self.clustering_hierarchy.groups},
            )
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
        Mutates ``root_analysis.components_relations`` in place, and applies the same
        frontier-wide ownership gate to every sub-scope's relations.
        """
        if not self.static_analysis:
            return []
        cfg_graphs = {str(lang): self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()}
        global_relations = build_global_relations(root_analysis, sub_analyses, cfg_graphs)
        ownership = ComponentOwnershipIndex.from_node_owners(
            build_global_node_to_component_map(root_analysis, sub_analyses)
        )
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
                ownership.owner_of,
                StaticReferenceResolver(self.repo_location, self.static_analysis).keep_relation_edge,
                self._changed_members,
            )
        root_analysis.components_relations = global_relations
        for sub_analysis in sub_analyses.values():
            sub_analysis.components_relations = drop_misattributed_edges(
                sub_analysis.components_relations, ownership.owner_of
            )
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
        absorbed_ids = absorb_single_child_components(root_analysis, sub_analyses)
        if self.clustering_hierarchy is not None:
            self.clustering_hierarchy.reroot_indexes(absorbed_ids)
        if self.tree_spec is not None:
            self.tree_spec.reroot(absorbed_ids)
        assert_scope_containment(root_analysis, sub_analyses)

    def finalize_and_save(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        *,
        persist_side_artifacts: bool = True,
        preserved_expandable_ids: Collection[str] = (),
    ) -> Path:
        """Shared post-analysis tail for every flow: finalize, persist, return the path.

        ``finalize_for_save`` then ``save_analysis`` (stamped with the current
        ``source_tree_hash`` and file-coverage summary). ``persist_side_artifacts``
        writes ``file_coverage.json``, the static-analysis cache, and
        ``fingerprint.json``. The partial flow leaves source-state sidecars
        unchanged and persists its updated lineage after this save.
        """
        self.finalize_for_save(root_analysis, sub_analyses)
        if persist_side_artifacts:
            source_tree_hash = self._source_tree_hash()
        else:
            # Partial keeps the prior hash so metadata matches the unchanged fingerprint.
            prior_metadata = load_analysis_metadata(Path(self.output_dir)) or {}
            source_tree_hash = prior_metadata.get("source_tree_hash", "") or self._source_tree_hash()
        expandable_component_ids, sub_expandable_ids = self._expandable_ids_for_tree(
            root_analysis,
            sub_analyses,
            preserved_expandable_ids,
        )
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
            tree_spec=self._tree_spec_dict(),
        ).resolve()
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

    def _apply_incremental_hierarchy(
        self,
        clustering: ClusterScopeResult,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> RecursiveScopeUpdateResult:
        """Apply persisted-scope updates by traversing one precomputed hierarchy."""
        assert self.incremental_updater is not None
        scope = root_analysis if clustering.scope_id == ROOT_SCOPE_ID else sub_analyses.get(clustering.scope_id)
        if scope is None:
            return RecursiveScopeUpdateResult()

        decision = plan_scope_result_update(scope, clustering, self._changed_members)
        applied = self.incremental_updater.update_scope(
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
        """Update an existing analysis by replaying its tree specification over the live names."""
        persisted_scopes = {ROOT_SCOPE_ID: root_analysis, **sub_analyses}
        if self.static_analysis is None:
            self.prepare_analysis(incremental=True, persisted_scopes=persisted_scopes)
        elif self._incremental_preparation is None:
            self._incremental_preparation = self._prepare_incremental_clustering(
                root_analysis,
                sub_analyses,
                self.depth_level,
            )
            if self.incremental_updater is None:
                self.agent_init()
        assert self.static_analysis is not None
        assert self._incremental_preparation is not None
        preparation = self._incremental_preparation
        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            if not preparation.has_changes:
                logger.info("Cluster and group membership deltas are empty; rewriting without re-detailing.")
                # No structural change, but a body-only edit still moves content
                # hashes — refresh the files index from live source so they don't
                # go stale (relations are already the global set here).
                # Re-scope anyway: a baseline written before child scopes were
                # confined to their parent stays drifted until something repairs it.
                self._rescope_child_analyses(root_analysis, sub_analyses, set())
                self._refresh_files_index(root_analysis, sub_analyses)
                return self.finalize_and_save(root_analysis, sub_analyses)

            assert self.incremental_updater is not None
            assert self.clustering_hierarchy is not None
            hierarchy = self.clustering_hierarchy
            baseline_membership = preparation.baseline_membership
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
            new_components = [
                component
                for component in created_components
                if component.component_id in hierarchy.preclustered_scopes
                and _component_depth(component.component_id) < self.depth_level
            ]
            generated_scope_ids: frozenset[str] = frozenset()
            if new_components:
                existing_scope_ids = set(sub_analyses)
                _, redetailed_subs = self._generate_subcomponents(root_analysis, new_components, sub_analyses)
                _merge_sub_analyses(sub_analyses, redetailed_subs)
                generated_scope_ids = frozenset(set(redetailed_subs) - existing_scope_ids)

            if apply_result.relation_contexts:
                # Each context froze its changed set when its scope was planned, before the
                # copy-forward pass proved some of those components byte-identical and dropped
                # them from the refresh set. Narrow the contexts to what actually changed, or
                # relations between two restored components would be reworded for nothing.
                settled = apply_result.refresh_ids | apply_result.new_component_ids | apply_result.removed_ids
                self.incremental_updater.generate_all_scope_relations(
                    root_analysis,
                    sub_analyses,
                    {
                        scope_id: replace(context, changed_ids=context.changed_ids & settled)
                        for scope_id, context in apply_result.relation_contexts.items()
                    },
                    self._changed_members,
                    self._changed_unattributed_files,
                )

            self._enrich_incremental_scopes(
                hierarchy,
                root_analysis,
                sub_analyses,
                apply_result.refresh_ids | apply_result.new_component_ids,
                baseline_membership,
                generated_scope_ids,
            )

            self._refresh_files_index(root_analysis, sub_analyses)

            analysis_path = self.finalize_and_save(root_analysis, sub_analyses)
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


def _merge_sub_analyses(
    target: dict[str, AnalysisInsights],
    updates: dict[str, AnalysisInsights],
) -> None:
    """Merge *updates* into *target*, preserving components absent from an update.

    In the incremental path, scoped operations may have inserted brand-new
    components outside the regenerated sub-analysis. A plain ``dict.update()``
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
