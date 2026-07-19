import json
import logging
import os
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    MetaAnalysisInsights,
    Relation,
    RelationEdge,
    SourceCodeReference,
)
from agents.cluster_methods_mixin import scoped_snapshot_from_lineage
from agents.details_agent import DetailsAgent
from agents.incremental_agent import (
    IncrementalAgent,
    prune_empty_components,
    remove_deleted_files,
)
from agents.incremental_planning_agent import IncrementalPlanningAgent
from agents.incremental_results import RecursiveScopeUpdateResult
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from agents.llm_config import initialize_llms
from agents.llm_errors import LLMAuthError
from agents.meta_agent import MetaAgent
from agents.planner_agent import get_expandable_components
from agents.relation_edges import index_relation_endpoints
from agents.scope_ids import ROOT_SCOPE_ID
from agents.content_hash import SourceCache, hash_repo_source_files, tree_hash_from_file_hashes
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.cluster_delta import (
    ClusterDelta,
    LanguageDelta,
    StructuralClusterDiff,
    compute_cluster_delta,
    structural_diff_from_delta,
)
from diagram_analysis.cluster_snapshot import (
    ClusterSnapshot,
    snapshot_from_static_analysis,
)
from diagram_analysis.exceptions import IncrementalCacheMissingError, ScopeContainmentError
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.file_index import build_files_index, changed_member_qnames, refresh_method_spans_from_cfg
from diagram_analysis.io_utils import load_analysis_metadata, normalize_repo_path, save_analysis, write_fingerprint
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import get_monitoring_run_dir
from repo_utils.change_detector import ChangeSet
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer, get_static_analysis
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_relations import build_global_relations, is_self_or_descendant
from static_analyzer.constants import Language
from static_analyzer.clustering import ClusterResult
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


def _owned_method_keys(components: Iterable[Component]) -> set[tuple[str, str]]:
    """The ``(file_path, qualified_name)`` set the given components collectively own."""
    return {
        (group.file_path, method.qualified_name)
        for component in components
        for group in component.file_methods
        for method in group.methods
    }


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
            for group in child.file_methods:
                group.methods = [m for m in group.methods if (group.file_path, m.qualified_name) not in departed]
            child.file_methods = [group for group in child.file_methods if group.methods]
    if entered:
        parent_methods = {
            (group.file_path, method.qualified_name): method
            for group in parent.file_methods
            for method in group.methods
        }
        _graft_entered_methods(child_scope, entered, parent_methods)
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

    # scope_id -> (file_path, qname) -> owning component_id
    owner_by_scope: dict[str, dict[tuple[str, str], str]] = field(default_factory=dict)
    # scope_id -> (file_path, qname) -> the baseline method entry (restored verbatim)
    entry_by_scope: dict[str, dict[tuple[str, str], MethodEntry]] = field(default_factory=dict)
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


def _capture_membership_baseline(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> _MembershipBaseline:
    """Snapshot per-scope method ownership and per-component metadata before the update.

    The incremental re-partitions clusters, which can shuffle unchanged methods between
    components. This snapshot lets a later pass pin every unchanged method back to the
    component that owned it and restore the metadata of components that end up identical.
    """
    baseline = _MembershipBaseline()
    for scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        if scope_id != ROOT_SCOPE_ID:
            baseline.scope_by_id[scope_id] = analysis.model_copy(deep=True)
        owner = baseline.owner_by_scope.setdefault(scope_id, {})
        entries = baseline.entry_by_scope.setdefault(scope_id, {})
        for component in analysis.components:
            if not component.component_id:
                continue
            keys: set[tuple[str, str]] = set()
            qnames: set[str] = set()
            for group in component.file_methods:
                for method in group.methods:
                    key = (group.file_path, method.qualified_name)
                    owner[key] = component.component_id
                    entries[key] = method.model_copy(deep=True)
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


def _restore_unchanged_membership(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline: _MembershipBaseline,
    changed_members: set[str],
    protected_ids: set[str],
) -> None:
    """Pin every unchanged method back to the component that owned it in the baseline.

    A method whose body did not change (absent from ``changed_members``) and whose baseline
    owner still exists is returned to that owner, overriding wherever the re-partition placed
    it — this is what stops an untouched top-level component from silently gaining or losing
    methods. Body-changed methods, added methods, methods whose owner was removed, and every
    method inside a freshly created component (``protected_ids``) keep the re-partition's
    placement, so a genuinely changed component still re-clusters. Each live method resolves
    to exactly one owner, so nothing is dropped or duplicated.
    """
    for scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        owner = baseline.owner_by_scope.get(scope_id, {})
        entries = baseline.entry_by_scope.get(scope_id, {})
        live_ids = {component.component_id for component in analysis.components if component.component_id}
        assigned: dict[str, dict[str, list[MethodEntry]]] = defaultdict(lambda: defaultdict(list))
        for component in analysis.components:
            protected = component.component_id in protected_ids
            for group in component.file_methods:
                for method in group.methods:
                    key = (group.file_path, method.qualified_name)
                    base_owner = owner.get(key)
                    if not protected and method.qualified_name not in changed_members and base_owner in live_ids:
                        assigned[base_owner][group.file_path].append(entries.get(key, method))
                    else:
                        assigned[component.component_id][group.file_path].append(method)
        for component in analysis.components:
            by_file = assigned.get(component.component_id, {})
            component.file_methods = [
                FileMethodGroup(
                    file_path=file_path,
                    methods=sorted(methods, key=lambda m: (m.start_line, m.end_line, m.qualified_name)),
                )
                for file_path, methods in sorted(by_file.items())
            ]


def _restore_unchanged_metadata(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline: _MembershipBaseline,
    changed_members: set[str],
) -> set[str]:
    """Restore name/description/key_entities of components identical to their baseline.

    A component with the same membership as the baseline and no body-changed member did not
    genuinely change; the planner may still have reworded it. Restoring its metadata and
    returning its id lets the caller drop it from the refresh set, so its relations to other
    unchanged components are carried over verbatim rather than re-derived.
    """
    unchanged_ids: set[str] = set()
    for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        for component in analysis.components:
            meta = baseline.meta_by_id.get(component.component_id)
            if meta is None:
                continue
            final_keys = frozenset(
                (group.file_path, method.qualified_name) for group in component.file_methods for method in group.methods
            )
            if final_keys != meta.member_keys or (meta.member_qnames & changed_members):
                continue
            component.name = meta.name
            component.description = meta.description
            component.key_entities = [entity.model_copy(deep=True) for entity in meta.key_entities]
            component.source_group_names = list(meta.source_group_names)
            # Restore the paired cluster ids too: membership is baseline-identical here, so the
            # repartition's ids would leave the component claiming clusters it no longer owns and
            # misroute the next incremental's changes for them.
            component.source_cluster_ids = list(meta.source_cluster_ids)
            unchanged_ids.add(component.component_id)
    return unchanged_ids


def _fully_unchanged_component_ids(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline: _MembershipBaseline,
    changed_members: set[str],
    protected_ids: set[str],
) -> set[str]:
    """Ids of components whose entire subtree is byte-identical to the baseline.

    A component qualifies when, at every depth, no member changed and it neither gained nor
    lost one. Containment (parent is a superset of every descendant) means a component's own top-level
    member set already spans its whole subtree, so testing that set is enough: no member
    qname is in ``changed_members`` and the live keys equal the baseline. A subtree holding
    a freshly created component is excluded — restoring it verbatim would delete that
    component, and new components are never restored.
    """
    fully_unchanged: set[str] = set()
    for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        for component in analysis.components:
            cid = component.component_id
            meta = baseline.meta_by_id.get(cid)
            if meta is None or meta.member_qnames & changed_members:
                continue
            if any(is_self_or_descendant(protected_id, cid) for protected_id in protected_ids):
                continue
            live_keys = frozenset(
                (group.file_path, method.qualified_name) for group in component.file_methods for method in group.methods
            )
            if live_keys == meta.member_keys:
                fully_unchanged.add(cid)
    return fully_unchanged


def _restore_unchanged_subtrees(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    baseline: _MembershipBaseline,
    changed_members: set[str],
    protected_ids: set[str],
) -> set[str]:
    """Restore the whole child-scope subtree of every fully-unchanged component, verbatim.

    ``_restore_unchanged_membership`` pins top-level ownership, but a component's child
    sub-components live in separate scopes that the re-partition — or the later
    ``_rescope_child_analyses`` reconcile — can still reshuffle, moving a method from one
    child to a sibling. That shifts the node->deepest-component map and churns the
    deepest-granularity relations even though nothing in the component changed. For every
    component whose subtree has no changed member, replace each descendant scope with its
    baseline deep copy so which method sits in which child is identical to the baseline.

    Returns the full set of preserved ids so the caller can skip them in the reconcile pass;
    the restore itself only rewrites each maximal subtree once (restoring a root already
    covers its descendants).
    """
    fully_unchanged = _fully_unchanged_component_ids(
        root_analysis, sub_analyses, baseline, changed_members, protected_ids
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
) -> set[str]:
    """Component ids whose global relations may legitimately differ from the baseline.

    A live component counts as changed when it is absent from the baseline (freshly
    created), owns a body-changed member, or its live member-key set differs from the
    baseline — it gained or lost a member. Membership churn alone (a new caller of another
    component, or the last caller removed) relabels the edges between the two components
    even with no surviving body-hash change, so it must be treated as changed or the
    genuinely-new edge is dropped / the stale baseline edge restored. Because every ancestor
    scope lists a method in its own ``file_methods``, the owner of a change and all of its
    ancestors are captured together. Everything else is preserved verbatim.
    """
    changed: set[str] = set()
    for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses):
        for component in analysis.components:
            component_id = component.component_id
            if not component_id:
                continue
            live_keys = frozenset(
                (group.file_path, method.qualified_name) for group in component.file_methods for method in group.methods
            )
            body_changed = any(
                method.qualified_name in changed_members for group in component.file_methods for method in group.methods
            )
            membership_changed = live_keys != baseline_member_keys.get(component_id, frozenset())
            if component_id not in baseline_component_ids or body_changed or membership_changed:
                changed.add(component_id)
    return changed


def _merge_edges_method_granular(
    fresh: Relation,
    base: Relation | None,
    re_extracted_methods: set[str],
    deleted_methods: set[str],
    live_methods: set[str],
) -> Relation | None:
    """Rebuild a changed pair's edges at method granularity.

    The static call-edge extraction is non-deterministic: re-analysing a file yields a
    different edge set each run even when its code is byte-identical, so taking the fresh
    rebuild wholesale churns edges for methods that did not change. Here an edge survives
    from the fresh rebuild only when its *source method* was actually re-extracted this run
    — body-changed or newly added. Every other edge is taken from the baseline, so an
    unchanged method keeps exactly the edges it had. Edges whose source or target method was
    deleted (or is otherwise gone from the live tree) are dropped, which is how a removal
    retracts its relations without inventing new ones. Returns ``None`` when nothing
    survives, so an emptied relation is dropped rather than left as a shell.
    """

    def live_edge(edge: RelationEdge) -> bool:
        src = edge.source.qualified_name
        dst = edge.target.qualified_name
        return src not in deleted_methods and dst not in deleted_methods and src in live_methods and dst in live_methods

    def collect(fresh_edges: list[RelationEdge], base_edges: list[RelationEdge]) -> list[RelationEdge]:
        merged = [e for e in fresh_edges if e.source.qualified_name in re_extracted_methods and live_edge(e)]
        seen = {(e.source.qualified_name, e.target.qualified_name) for e in merged}
        for e in base_edges:
            key = (e.source.qualified_name, e.target.qualified_name)
            if e.source.qualified_name not in re_extracted_methods and live_edge(e) and key not in seen:
                merged.append(e)
                seen.add(key)
        return merged

    all_edges = collect(fresh.all_edges, base.all_edges if base is not None else [])
    key_edges = collect(fresh.key_edges, base.key_edges if base is not None else [])
    if not all_edges:
        return None
    return fresh.model_copy(update={"all_edges": all_edges, "key_edges": key_edges})


def _preserve_unchanged_global_relations(
    rebuilt_relations: list[Relation],
    baseline_by_pair: dict[tuple[str, str], Relation],
    changed_component_ids: set[str],
    live_ids: set[str],
    re_extracted_methods: set[str],
    deleted_methods: set[str],
    live_methods: set[str],
) -> list[Relation]:
    """Carry a global relation over from the baseline when neither endpoint changed.

    The save-time rebuild re-derives every relation at the deepest granularity, re-labelling
    even edges between two untouched components. For each pair whose endpoints are both
    unchanged we drop the rebuilt edge and take the baseline verbatim. For a pair that DOES
    touch a changed component we no longer take the fresh rebuild wholesale — that would
    churn the edges of the pair's unchanged methods, because extraction is non-deterministic
    — but merge it against the baseline at method granularity (see
    ``_merge_edges_method_granular``): only a body-changed or newly-added method contributes
    fresh edges; unchanged methods keep their baseline edges; deleted methods' edges are
    dropped. Relations are keyed by ``(src_id, dst_id)``, the stable component identity.
    """

    def touches_change(src_id: str, dst_id: str) -> bool:
        return src_id in changed_component_ids or dst_id in changed_component_ids

    kept: list[Relation] = []
    for rel in rebuilt_relations:
        if not touches_change(rel.src_id, rel.dst_id):
            continue
        merged = _merge_edges_method_granular(
            rel,
            baseline_by_pair.get((rel.src_id, rel.dst_id)),
            re_extracted_methods,
            deleted_methods,
            live_methods,
        )
        if merged is not None:
            kept.append(merged)
    for (src_id, dst_id), relation in baseline_by_pair.items():
        if touches_change(src_id, dst_id) or src_id not in live_ids or dst_id not in live_ids:
            continue
        kept.append(relation)
    return sorted(kept, key=lambda rel: (rel.src_id, rel.dst_id))


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
        # Qnames whose method body changed vs the baseline, computed once per
        # incremental run. Drives the member-granular "modified" gate so a file's
        # methods dispersed across clusters only dirty the cluster they belong to.
        self._changed_members: set[str] = set()
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
        # diff base for the next warm-start (NOT a cache gate). ``pre_analysis``
        # fills it from the live tree when unset; ``None`` is a tag-less save.
        self.source_sha: str | None = None
        # Whole-tree ``{posix_path: sha16}`` fingerprint, computed once per run and
        # reused for source_sha, the sidecar, and every save's source_tree_hash
        # instead of re-walking the tree each time.
        self._source_tree_fingerprint: dict[str, str] | None = None
        self._static_analyzer = static_analyzer

        self.details_agent: DetailsAgent | None = None
        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.abstraction_agent: AbstractionAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.incremental_planning_agent: IncrementalPlanningAgent | None = None
        self.incremental_agent: IncrementalAgent | None = None
        self.meta_context: MetaAnalysisInsights | None = None
        self.file_coverage_data: dict | None = None

        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None

    @track_analysis
    def process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        return self._process_component(component)

    def _process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        """Process a single component and return its name, sub-analysis, and new components to analyze."""
        try:
            assert self.details_agent is not None

            analysis, _ = self.details_agent.run(component)

            # Track whether parent had clusters for expansion decision
            parent_had_clusters = bool(component.source_cluster_ids)

            # Get new components to analyze (deterministic, no LLM)
            new_components = get_expandable_components(analysis, parent_had_clusters=parent_had_clusters)

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
        """Make the post-delta partition the one the next run warm-starts from.

        The clusterer writes a snapshot while computing the delta, but the agent
        layer can still settle on a different partition; the pkl has to carry the
        one the analysis actually describes or the next run warm-starts from a
        partition nobody published.
        """
        if self.static_analysis is None:
            return
        for language, cr in cluster_results.items():
            try:
                graph = self.static_analysis.get_program_graph(Language(language))
            except (ValueError, KeyError):
                continue
            snapshot = graph.cluster_snapshot
            if snapshot is not None:
                snapshot.cluster_result = cr
                # Replace symbol membership, keep the file/package members the
                # warm start needs — cr only ever carries symbols.
                symbol_ids = {node.id for node in graph.symbol_nodes()}
                members_by_cluster = {
                    cluster_id: members - symbol_ids for cluster_id, members in snapshot.module_members.items()
                }
                for cluster_id, members in cr.clusters.items():
                    members_by_cluster.setdefault(cluster_id, set()).update(members)
                snapshot.module_members = {c: m for c, m in members_by_cluster.items() if m}
            graph.record_cluster_paths(cr)

    def _persist_static_analysis_artifact(self) -> None:
        """Persist the post-clustering static-analysis artifact."""
        if self._static_analyzer is not None:
            self._static_analyzer.flush_cache()
            return
        if self.static_analysis is None:
            return
        StaticAnalysisCache(self.output_dir, self.repo_location).save(self.static_analysis, source_sha=self.source_sha)

    def _source_tree_fingerprint_map(self) -> dict[str, str]:
        """The whole-tree fingerprint, fingerprinting on first use if pre_analysis didn't."""
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
            run_id=self.run_id,
        )
        self.abstraction_agent = AbstractionAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        self.incremental_planning_agent = IncrementalPlanningAgent(
            repo_dir=self.repo_location,
            static_analysis=static_analysis,
            project_name=self.repo_name,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            changes=self.changes,
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
                "IncrementalPlanningAgent": self.incremental_planning_agent,
                "IncrementalAgent": self.incremental_agent,
            }
        )

    def pre_analysis(self):
        analysis_start_time = time.time()

        # Fingerprint the whole tree once; source_sha, the sidecar, and every
        # save's source_tree_hash reuse it instead of re-walking per call.
        self._source_tree_fingerprint = hash_repo_source_files(self.repo_location)
        # Compute the source-state tag from live source when a caller didn't
        # supply one, so the pkl always gets a .sha sibling for the next
        # warm-start — no caller has to thread source_sha in.
        if self.source_sha is None:
            self.source_sha = self._source_tree_hash() or None

        # Initialize LLMs before spawning threads so both share the same instances
        agent_llm, parsing_llm = initialize_llms()

        self._initialize_meta_agent(agent_llm, parsing_llm)

        # Decide how to obtain static analysis results, then run it in parallel
        # with the meta-context computation so neither blocks the other.
        if self._static_analyzer is not None:
            logger.info("Using injected StaticAnalyzer (clients already running)")
            static_callable = self._get_static_with_injected_analyzer
        else:
            static_callable = self._get_static_with_new_analyzer

        with ThreadPoolExecutor(max_workers=2) as executor:
            meta_agent = self.meta_agent
            assert meta_agent is not None
            static_future = executor.submit(static_callable)
            meta_future = executor.submit(meta_agent.analyze_project_metadata, skip_cache=self.force_full_analysis)
            static_analysis = static_future.result()
            meta_context = meta_future.result()

        self.static_analysis = static_analysis
        self.meta_context = meta_context

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

        self._initialize_agents(static_analysis, meta_context, agent_llm, parsing_llm)

        if self.monitoring_enabled:
            monitoring_dir = get_monitoring_run_dir(self.log_path, create=True)
            logger.debug(f"Monitoring enabled. Writing stats to {monitoring_dir}")

            # Save code_stats.json
            code_stats_file = monitoring_dir / "code_stats.json"
            with open(code_stats_file, "w", encoding="utf-8") as f:
                json.dump(static_stats, f, indent=2)
            logger.debug(f"Written code_stats.json to {code_stats_file}")

            # Initialize streaming writer (handles timing and run_metadata.json)
            self.stats_writer = StreamingStatsWriter(
                monitoring_dir=monitoring_dir,
                agents_dict=self._monitoring_agents,
                repo_name=self.project_name or self.repo_name,
                output_dir=str(self.output_dir),
                start_time=analysis_start_time,
            )

    def _generate_subcomponents(
        self,
        analysis: AnalysisInsights,
        root_components: list[Component],
    ) -> tuple[list[Component], dict[str, AnalysisInsights]]:
        """Generate subcomponents using absolute component depth and a frontier queue."""
        max_workers = min(os.cpu_count() or 4, 8)

        expanded_components: list[Component] = []
        sub_analyses: dict[str, AnalysisInsights] = {}

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
                            save_analysis(
                                analysis=analysis,
                                output_dir=Path(self.output_dir),
                                sub_analyses=sub_analyses,
                                repo_name=self.repo_name,
                                repo_dir=self.repo_location,
                                source_tree_hash=self._source_tree_hash(),
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
        if self.details_agent is None or self.abstraction_agent is None:
            self.pre_analysis()

        # Start monitoring (tracks start time)
        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Generate the initial analysis
            logger.info("Generating initial analysis")

            assert self.abstraction_agent is not None

            analysis, cluster_results = self.abstraction_agent.run()
            # Get the initial components to analyze (deterministic, no LLM)
            root_components = get_expandable_components(analysis)
            logger.info(f"Found {len(root_components)} components to analyze at level 1")

            # Process components using a frontier queue: submit children as soon as parent finishes.
            expanded_components, sub_analyses = self._generate_subcomponents(analysis, root_components)

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
        cfg_graphs = {
            str(lang): self.static_analysis.get_program_graph(lang) for lang in self.static_analysis.get_languages()
        }
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
            )
            live_ids = {
                component.component_id
                for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses)
                for component in analysis.components
                if component.component_id
            }
            live_methods = {
                method.qualified_name
                for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses)
                for component in analysis.components
                for group in component.file_methods
                for method in group.methods
            }
            base_methods = {qname for keys in self._baseline_member_keys.values() for _file, qname in keys}
            deleted_methods = base_methods - live_methods
            # Only a body-changed or newly-added method has its edges re-extracted from the
            # (non-deterministic) fresh CFG; every unchanged method keeps its baseline edges.
            re_extracted_methods = self._changed_members | (live_methods - base_methods)
            global_relations = _preserve_unchanged_global_relations(
                global_relations,
                self._baseline_global_relations,
                changed_ids,
                live_ids,
                re_extracted_methods,
                deleted_methods,
                live_methods,
            )
        root_analysis.components_relations = global_relations
        return global_relations

    def finalize_for_save(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> None:
        """Prepare an analysis tree for its authoritative save.

        Single pre-save chokepoint shared by the full, incremental, and partial
        flows. All steps are idempotent and
        safe with an empty ``sub_analyses`` (rebuild is a root-only pass).
        """
        self.rebuild_global_relations(root_analysis, sub_analyses)
        self._strip_ignored(root_analysis, sub_analyses)
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
        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=Path(self.output_dir),
            sub_analyses=sub_analyses,
            repo_name=self.repo_name,
            file_coverage_summary=self._build_file_coverage_summary(),
            repo_dir=self.repo_location,
            source_tree_hash=source_tree_hash,
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
        structural_diff: StructuralClusterDiff,
        cluster_results: dict[str, ClusterResult],
        sub_analyses: dict[str, AnalysisInsights],
    ) -> RecursiveScopeUpdateResult:
        assert self.incremental_planning_agent is not None
        assert self.incremental_agent is not None
        decision = self.incremental_planning_agent.decide_scope_update(
            scope_id,
            scope,
            structural_diff,
            cluster_results,
        )
        apply_result = self.incremental_agent.update_scope(scope_id, scope, decision, cluster_results)
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
            child_cluster_results, child_diff = _build_scope_incremental_inputs(
                child_component,
                component_id,
                self.incremental_agent,
                self.changes,
                self.repo_location,
                self._changed_members,
            )
            if not child_diff.has_changes:
                continue
            if not _child_scope_needs_recursive_update(child_scope, child_diff):
                continue
            child_result = self._apply_incremental_scope_recursively(
                component_id,
                child_scope,
                child_diff,
                child_cluster_results,
                sub_analyses,
            )
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
        """Cluster-driven incremental update of an existing ``analysis.json``.

        Deterministic cluster delta, one LLM call to route delta clusters,
        then ``_generate_subcomponents`` seeded with the changed components.
        Raises when no trustworthy baseline or scoped update plan is available.
        """
        if self.details_agent is None or self.incremental_planning_agent is None or self.incremental_agent is None:
            self.pre_analysis()
        assert self.static_analysis is not None
        assert self.details_agent is not None
        assert self.incremental_planning_agent is not None
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
        # Per-component baseline member keys, captured pre-mutation so the save-time relation
        # rebuild sees the true before-state on BOTH the re-cluster path and the empty-delta
        # path (a body-only edit). Deleted methods are still present here, which is how a
        # removal becomes visible to the rebuild; an empty-delta run left this unset before,
        # so every component read as changed and its non-deterministic fresh edges were kept.
        self._baseline_member_keys = {
            component.component_id: frozenset(
                (group.file_path, method.qualified_name) for group in component.file_methods for method in group.methods
            )
            for _scope_id, analysis in _iter_incremental_scopes(root_analysis, sub_analyses)
            for component in analysis.components
            if component.component_id
        }

        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Scrub before cluster math: orphan-routed files never appear in
            # any cluster, so deletes wouldn't surface via the delta alone.
            live_files: set[str] = set()
            for language in self.static_analysis.get_languages():
                try:
                    cfg = self.static_analysis.get_program_graph(language)
                except (ValueError, KeyError):
                    continue
                for node in cfg.nodes.values():
                    if node.file_path:
                        live_files.add(normalize_repo_path(node.file_path, self.repo_location))
            remove_deleted_files(root_analysis, sub_analyses, live_files)

            snapshot_source = self.static_analysis.incremental_base_results or self.static_analysis
            old_snapshot = snapshot_from_static_analysis(snapshot_source)
            if not old_snapshot.all_cluster_ids() or old_snapshot.missing_snapshot_languages:
                # No cluster_cache on the live CFG — no prior pkl, legacy pkl,
                # or first-ever incremental run — or a partial baseline where
                # some language carries a snapshot and another with real code
                # does not. Refuse to silently rebuild from scratch; that would
                # discard the existing analysis.json's depth and component IDs,
                # or recluster the missing language and lose its stable ids.
                # Caller must explicitly request a full run instead.
                # ``IncrementalCacheMissingError`` inspects the artifact dir to
                # pick the specific diagnostic (missing pkl, missing sha, or
                # pkl-without-cluster-baseline).
                if old_snapshot.missing_snapshot_languages:
                    logger.error(
                        "[incremental] partial cluster baseline: %s have code but no snapshot",
                        sorted(old_snapshot.missing_snapshot_languages),
                    )
                artifact_dir = self.output_dir
                error = IncrementalCacheMissingError(artifact_dir)
                logger.error("%s", error)
                raise error

            # Body-edited qnames, derived from per-method content hashes so a
            # body-only edit surfaces even when the graph fingerprint (hence the
            # cluster membership) is unchanged. Must precede the delta: it seeds
            # the member-granular "modified" gate for the root and every child.
            self._changed_members = changed_member_qnames(
                [root_analysis, *sub_analyses.values()],
                self.static_analysis,
                self.repo_location,
                self.changes,
            )

            delta = compute_cluster_delta(
                old_snapshot,
                self.static_analysis,
                changes=self.changes,
                repo_dir=self.repo_location,
                changed_members=self._changed_members,
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

            structural_diff = structural_diff_from_delta(
                old_snapshot,
                delta,
                changes=self.changes,
                repo_dir=self.repo_location,
                changed_members=self._changed_members,
            )
            protected_empty_ids = _cluster_backed_empty_component_ids(root_analysis, sub_analyses)
            baseline_membership = _capture_membership_baseline(root_analysis, sub_analyses)
            apply_result = self._apply_incremental_scope_recursively(
                ROOT_SCOPE_ID,
                root_analysis,
                structural_diff,
                delta.cluster_results(),
                sub_analyses,
            )
            # Pin unchanged methods back to their baseline owner so the re-partition only
            # moves what genuinely changed, then freeze the whole subtree of any component
            # with no changed member so its sub-component boundaries can't drift, and finally
            # reconcile the child scopes that genuinely moved.
            _restore_unchanged_membership(
                root_analysis,
                sub_analyses,
                baseline_membership,
                self._changed_members,
                apply_result.new_component_ids,
            )
            preserved_ids = _restore_unchanged_subtrees(
                root_analysis,
                sub_analyses,
                baseline_membership,
                self._changed_members,
                apply_result.new_component_ids,
            )
            self._rescope_child_analyses(root_analysis, sub_analyses, preserved_ids)
            # A component identical to its baseline did not change: restore any metadata the
            # planner reworded and drop it from the refresh set so its relations carry over.
            unchanged_ids = _restore_unchanged_metadata(
                root_analysis, sub_analyses, baseline_membership, self._changed_members
            )
            apply_result.refresh_ids -= unchanged_ids

            removed_ids = prune_empty_components(root_analysis, sub_analyses, protected_empty_ids)
            if removed_ids:
                apply_result.refresh_ids -= removed_ids
                apply_result.new_component_ids -= removed_ids
            _drop_removed_subtree_analyses(sub_analyses, apply_result.removed_ids | removed_ids)

            new_components = [
                component
                for component in _collect_components_by_id(apply_result.new_component_ids, root_analysis, sub_analyses)
                if _component_depth(component.component_id) < self.depth_level
            ]
            if new_components:
                _, redetailed_subs = self._generate_subcomponents(root_analysis, new_components)
                _merge_sub_analyses(sub_analyses, redetailed_subs)

            if apply_result.relation_contexts:
                self.incremental_agent.generate_all_scope_relations(
                    root_analysis,
                    sub_analyses,
                    apply_result.relation_contexts,
                    apply_result.refresh_ids | apply_result.new_component_ids,
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
            violations.append(f"child scope {component_id!r} has no parent component in the tree")
            continue
        owned = _owned_method_keys([parent])
        for child in child_scope.components:
            escaped = _owned_method_keys([child]) - owned
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


def _cluster_backed_empty_component_ids(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> set[str]:
    protected_ids: set[str] = set()
    for analysis in [root_analysis, *sub_analyses.values()]:
        for component in analysis.components:
            if (
                component.component_id
                and component.source_cluster_ids
                and not component.key_entities
                and not any(group.methods for group in component.file_methods)
            ):
                protected_ids.add(component.component_id)
    return protected_ids


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
    removed_qnames: set[str] = set()
    for diff in structural_diff.by_language.values():
        for member_delta in [*diff.modified, *diff.new_details]:
            removed_qnames.update(member_delta.removed_methods)
    return bool(removed_qnames.intersection(owned_qnames))


def _build_scope_incremental_inputs(
    component: Component,
    scope_id: str,
    incremental_agent: IncrementalAgent,
    changes: ChangeSet | None,
    repo_dir: Path,
    changed_members: set[str],
) -> tuple[dict[str, ClusterResult], StructuralClusterDiff]:
    old_snapshot = scoped_snapshot_for_component(component, scope_id, incremental_agent)
    if not old_snapshot.all_cluster_ids():
        return {}, StructuralClusterDiff()

    _subgraph_str, cluster_results, _subgraph_cfgs = incremental_agent._create_strict_component_subgraph(
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
        changed_members=changed_members,
    )
    return cluster_results, structural_diff


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
        cfg = incremental_agent.static_analysis.get_program_graph(language)
        sub_cfg = cfg.filter_by_nodes(assigned_qnames)
        if sub_cfg.nodes:
            by_language[str(language)] = scoped_snapshot_from_lineage(sub_cfg, scope_id)
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
