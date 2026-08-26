"""Incremental refresh helpers for scoped structural updates."""

import logging
import os
import threading
from collections.abc import Collection
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    ComponentApiSurfaces,
    ComponentArchitecture,
    ComponentRelations,
    MetaAnalysisInsights,
    Relation,
    ScopeOperation,
    ScopeOperationAction,
    ScopeUpdateDecision,
    assign_component_ids,
    assign_relation_ids,
    iter_components,
)
from agents.component_ownership import ComponentOwnershipIndex, group_ids_by_name
from agents.content_hash import SourceCache
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.incremental_results import ScopeRelationContext, ScopeUpdateResult
from agents.llm_renderers import render_cluster_groups, render_scope_connections
from agents.prompts import (
    format_project_system_message,
    get_api_surfaces_message,
    get_final_analysis_message,
    get_relation_analysis_message,
    get_system_message,
)
from agents.relation_edges import index_relation_endpoints, preserve_unchanged_relations
from agents.scope_ids import ROOT_SCOPE_ID
from clustering_ids import CodeBoardingClusterIds
from agents.static_analysis_enricher_mixin import StaticAnalysisEnricherMixin
from agents.validation import ValidationContext, validate_relations
from diagram_analysis.file_index import build_file_methods_from_nodes, build_files_index
from monitoring import trace
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterScopeResult

logger = logging.getLogger(__name__)


class IncrementalAgent(StaticAnalysisEnricherMixin, CodeBoardingAgent):
    """Materialize incremental plans and regenerate touched scope relations."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights | None,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        component_ownership: ComponentOwnershipIndex,
        changes: ChangeSet | None = None,
    ):
        system_message = format_project_system_message(get_system_message(), project_name, meta_context)
        super().__init__(repo_dir, static_analysis, system_message, agent_llm, parsing_llm)
        if changes is not None:
            self.toolkit.context.changes = changes
        self.component_ownership = component_ownership
        self.project_name = project_name
        self.meta_context = meta_context
        self.prompts = {
            "new_component_details": PromptTemplate(
                template=get_final_analysis_message(),
                input_variables=["cluster_analysis"],
            ),
            "api_surfaces": PromptTemplate(
                template=get_api_surfaces_message(),
                input_variables=[
                    "component_summaries",
                    "static_call_evidence",
                ],
            ),
            "relation_analysis": PromptTemplate(
                template=get_relation_analysis_message(),
                input_variables=[
                    "component_summaries",
                    "api_surfaces",
                    "static_call_evidence",
                ],
            ),
        }

    @trace
    def update_scope(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        decision: ScopeUpdateDecision,
        clustering: ClusterScopeResult,
    ) -> ScopeUpdateResult:
        """Apply a planning decision to one scope and refresh its derived fields."""
        components_by_id = {
            component.component_id: component for component in scope.components if component.component_id
        }
        refresh_ids = _remove_reassigned_clusters(
            scope_id,
            scope.components,
            components_by_id,
            decision,
        )
        new_component_ids: set[str] = set()
        removed_ids: set[str] = set()

        for operation in decision.operations:
            if operation.action == ScopeOperationAction.CREATE_COMPONENT:
                self._create_component_from_operation(
                    scope_id,
                    scope,
                    operation,
                    components_by_id,
                    refresh_ids,
                    new_component_ids,
                )
                continue
            if operation.action == ScopeOperationAction.DELETE_COMPONENT:
                if operation.component_id:
                    component = components_by_id.get(operation.component_id)
                    if component is not None and _component_has_live_cfg_methods(
                        component, _live_cfg_qnames(self.static_analysis)
                    ):
                        refresh_ids.add(operation.component_id)
                        continue
                    removed_ids.add(operation.component_id)
                continue
            if operation.action == ScopeOperationAction.NOOP:
                component = components_by_id.get(operation.component_id or "")
                if component is None:
                    continue
                self._sync_noop_component_cluster_ids(scope_id, component, operation, refresh_ids)
                continue

            component = components_by_id.get(operation.component_id or "")
            if component is None:
                continue
            self._update_component_from_operation(scope_id, component, operation)
            if component.component_id:
                refresh_ids.add(component.component_id)

        if removed_ids:
            scope.components = [
                component for component in scope.components if component.component_id not in removed_ids
            ]
            _strip_relations(scope, removed_ids)

        touched_ids = refresh_ids | new_component_ids
        if touched_ids:
            self._patch_scope_file_methods(scope, clustering, touched_ids)
            self.reference_resolver.fix_key_entities_refs(scope, touched_ids)

        _log_duplicate_cluster_ownership(scope_id, scope.components)

        return ScopeUpdateResult(
            relation_context=ScopeRelationContext(
                clustering=clustering,
                changed_ids=frozenset(refresh_ids | new_component_ids | removed_ids),
            ),
            refresh_ids=refresh_ids,
            new_component_ids=new_component_ids,
            removed_ids=removed_ids,
        )

    def _create_component_from_operation(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        operation: ScopeOperation,
        components_by_id: dict[str, Component],
        refresh_ids: set[str],
        new_component_ids: set[str],
    ) -> None:
        source_cluster_ids = _operation_source_cluster_ids(scope_id, operation)
        if not source_cluster_ids:
            logger.error(
                "[incremental] skipping create_component with no cluster refs for scope %s; refs=%s",
                scope_id or "root",
                [ref.llm_str() for ref in operation.cluster_refs],
            )
            return

        component = Component(
            name=operation.name or "New Component",
            description=operation.description or "",
            key_entities=operation.key_entities,
            source_group_names=[operation.name or "New Component"],
            source_cluster_ids=source_cluster_ids,
        )
        scope.components.append(component)
        assign_component_ids(scope, parent_id=_component_id_parent(scope_id), only_new=True)
        if component.component_id:
            refresh_ids.add(component.component_id)
            new_component_ids.add(component.component_id)
            components_by_id[component.component_id] = component

    def _sync_noop_component_cluster_ids(
        self,
        scope_id: str,
        component: Component,
        operation: ScopeOperation,
        refresh_ids: set[str],
    ) -> None:
        merged_cluster_ids = CodeBoardingClusterIds.sort(
            set(component.source_cluster_ids) | set(_operation_source_cluster_ids(scope_id, operation))
        )
        if merged_cluster_ids != component.source_cluster_ids and component.component_id:
            refresh_ids.add(component.component_id)
        component.source_cluster_ids = merged_cluster_ids

    @trace
    def detail_new_components(self, components: list[Component]) -> None:
        """Replace new components' provisional names and descriptions in one LLM call."""
        target_by_group: dict[str, Component] = {}
        group_ids: dict[str, list[int]] = {}
        descriptions: dict[str, str] = {}
        for component in components:
            group_name = f"Incremental Group {component.component_id}"
            group_ids[group_name] = []
            descriptions[group_name] = _new_component_membership_summary(component)
            target_by_group[group_name.casefold()] = component

        prompt = self.prompts["new_component_details"].format(
            cluster_analysis=render_cluster_groups(group_ids, descriptions)
        )
        group_names = list(group_ids)
        prompt += (
            f"\n\n## New Component Groups ({len(group_names)} total)\n"
            f"Return exactly one semantically named component for each of these fixed groups: {group_names}.\n"
            "These components have final deterministic membership. Replace their provisional metadata; "
            "do not merge, split, or reassign their symbols."
        )
        architecture = self._parse_invoke(prompt, ComponentArchitecture)
        for detailed in architecture.components:
            if len(detailed.source_group_names) != 1:
                continue
            target = target_by_group.get(detailed.source_group_names[0].casefold())
            if target is None:
                continue
            name = detailed.name.strip()
            description = detailed.description.strip()
            if name and description:
                target.name = name
                target.description = description

    def _update_component_from_operation(
        self,
        scope_id: str,
        component: Component,
        operation: ScopeOperation,
    ) -> None:
        if operation.name:
            component.name = operation.name
        if operation.description:
            component.description = operation.description
        if operation.key_entities:
            component.key_entities = operation.key_entities
        # Replace, don't union. The planner emits the component's complete new cluster set on
        # every UPDATE, so a cluster it no longer lists is gone — merged with the prior ids, a
        # deleted cluster would cling to the component and mis-anchor a later reused id.
        # ``_remove_reassigned_clusters`` already stripped these ids from every component,
        # including this one, so assigning the operation's refs is the whole set.
        component.source_cluster_ids = CodeBoardingClusterIds.sort(
            set(_operation_source_cluster_ids(scope_id, operation))
        )

    def _patch_scope_file_methods(
        self,
        scope: AnalysisInsights,
        clustering: ClusterScopeResult,
        touched_ids: set[str],
    ) -> None:
        source_cache: SourceCache = {}
        patched_groups: dict[str, list[FileMethodGroup]] = {}
        for group in clustering.groups:
            if group.group_id not in touched_ids:
                continue
            nodes = [
                clustering.graphs_by_language[language].nodes[qualified_name]
                for language, members in group.symbol_members_by_language.items()
                if language in clustering.graphs_by_language
                for qualified_name in sorted(members)
                if qualified_name in clustering.graphs_by_language[language].nodes
            ]
            patched_groups[group.group_id] = build_file_methods_from_nodes(nodes, self.repo_dir, source_cache)
        _patch_file_methods(scope, patched_groups, touched_ids, _live_cfg_qnames(self.static_analysis))
        scope.files = build_files_index(scope, self.repo_dir, source_cache)

    @trace
    def step_api_surfaces(
        self,
        scope: AnalysisInsights,
        scope_name: str,
        static_call_evidence: str,
    ) -> ComponentApiSurfaces:
        """Analyze API surfaces for one updated scope."""
        logger.info("[IncrementalAgent] Analyzing API surfaces for scope: %s", scope_name)
        prompt = self.prompts["api_surfaces"].format(
            component_summaries=ComponentArchitecture(
                description=scope.description, components=scope.components
            ).llm_str(),
            static_call_evidence=static_call_evidence,
        )
        return self._parse_invoke(prompt, ComponentApiSurfaces)

    @trace
    def step_relation_analysis(
        self,
        scope: AnalysisInsights,
        scope_name: str,
        api_surfaces: ComponentApiSurfaces,
        clustering: ClusterScopeResult,
        static_call_evidence: str,
    ) -> list[Relation]:
        """Discover evidence-backed relations and attach deterministic CFG edges."""
        logger.info("[IncrementalAgent] Discovering component relations for scope: %s", scope_name)
        cluster_results = clustering.leaf_clusters_by_language
        cfg_graphs = clustering.graphs_by_language
        self.toolkit.context.clustering = clustering
        self.toolkit.context.group_ids_by_name = group_ids_by_name(
            scope.components, {group.group_id for group in clustering.groups}
        )
        self.toolkit.context.cluster_results = cluster_results
        self.toolkit.context.cfg_graphs = cfg_graphs
        prompt = self.prompts["relation_analysis"].format(
            component_summaries=ComponentArchitecture(
                description=scope.description, components=scope.components
            ).llm_str(),
            api_surfaces=api_surfaces.llm_str(),
            static_call_evidence=static_call_evidence,
        )
        relation_result: ComponentRelations = self._invoke_validate(
            prompt,
            ComponentRelations,
            validators=[validate_relations],
            validation_context=ValidationContext(
                cluster_results=cluster_results,
                cfg_graphs=cfg_graphs,
                repo_dir=str(self.repo_dir),
                static_analysis=self.static_analysis,
                components=scope.components,
            ),
            max_validation_attempts=3,
        )
        scope.components_relations = relation_result.components_relations
        self._attach_static_relations(scope, clustering)
        return relation_result.components_relations

    def _attach_static_relations(
        self,
        scope: AnalysisInsights,
        clustering: ClusterScopeResult,
    ) -> None:
        """Ground the scope's relations in the live CFG and resolve their source references.

        Why: shared by the LLM and no-change paths; the latter passes no LLM relations, so only
        the deterministic static call edges remain.
        """
        assign_relation_ids(scope)
        self.merge_scope_relations(scope, clustering)
        self.reference_resolver.fix_source_code_reference_lines(scope)
        index_relation_endpoints(scope, self.repo_dir)

    @trace
    def generate_scope_relations(
        self,
        scope: AnalysisInsights,
        scope_name: str,
        context: ScopeRelationContext,
        changed_members: set[str] | None = None,
        unattributed_files: Collection[str] = (),
    ) -> list[Relation]:
        """Run the API-surface and relation stages for one updated scope, or skip both when nothing in it changed."""
        if len(scope.components) < 2:
            scope.components_relations = []
            self.reference_resolver.fix_source_code_reference_lines(scope)
            return []

        # Snapshot before regeneration: the rebuild replaces the scope's relations wholesale, so
        # every edge between two untouched components would come back reworded.
        baseline_by_pair = {
            (relation.src_id, relation.dst_id): relation.model_copy(deep=True)
            for relation in scope.components_relations
            if relation.src_id and relation.dst_id
        }
        # Also keyed by NAME. Ids are assigned per run by `assign_relation_ids`, so a
        # re-partition can hand the same two components different numbers; the id lookup then
        # misses and the pair is treated as brand new, which re-rolls its wording. Measured on
        # `referenced-symbol-deleted`: 9 statically-backed relations whose call set was
        # byte-identical came back re-worded for exactly this reason. Names survive
        # renumbering, so they are the fallback identity.
        baseline_by_names = {
            (relation.src_name, relation.dst_name): relation.model_copy(deep=True)
            for relation in scope.components_relations
            if relation.src_name and relation.dst_name
        }

        gated_members: set[str] = set()
        if context.changed_ids:
            # Rendered once and shared: both prompts must agree on what the commit touched.
            gated_members = set() if unattributed_files else changed_members or set()
            static_call_evidence = render_scope_connections(
                context.clustering,
                {component.component_id: component.name for component in scope.components},
                gated_members,
                baseline_by_pair,
            )
            api_surfaces = self.step_api_surfaces(scope, scope_name, static_call_evidence)
            rels = self.step_relation_analysis(
                scope,
                scope_name,
                api_surfaces,
                context.clustering,
                static_call_evidence,
            )
        else:
            # Nothing in this scope changed, so preserve_unchanged_relations below would discard any
            # LLM output anyway; skip both round-trips and rebuild edges from the live CFG.
            scope.components_relations = []
            self._attach_static_relations(scope, context.clustering)
            rels = scope.components_relations

        if baseline_by_pair:
            live_ids = {component.component_id for component in scope.components if component.component_id}
            # A relation is ADDED or REMOVED because code changed, not because a partitioner
            # moved untouched methods around. `context.changed_ids` carries both — it is the
            # right scope to re-derive, and the wrong reason to change the edge set — so the
            # preservation gate sees only components the COMMIT reached. Components that are
            # new or gone keep their ids here: they are changes the commit does account for.
            changed_component_ids = {
                component.component_id
                for component in scope.components
                if component.component_id
                and any(
                    method.qualified_name in (changed_members or set())
                    for group in component.file_methods
                    for method in group.methods
                )
            }
            commit_changed = changed_component_ids | (set(context.changed_ids) - live_ids)
            live_qnames = {
                qualified_name
                for cluster_result in context.cluster_results.values()
                for members in cluster_result.clusters.values()
                for qualified_name in members
            }
            for relation in scope.components_relations:
                # Carry the baseline forward when regenerated component ids changed but names did not.
                pair = (relation.src_id, relation.dst_id)
                if pair not in baseline_by_pair:
                    by_name = baseline_by_names.get((relation.src_name, relation.dst_name))
                    if by_name is not None:
                        baseline_by_pair[pair] = by_name
            scope.components_relations = preserve_unchanged_relations(
                scope.components_relations,
                baseline_by_pair,
                commit_changed,
                live_ids,
                live_qnames,
                changed_members,
                gated_members,
            )
            # Preservation runs after the grounding filters and re-injects baseline edges, so
            # the assembled list is filtered once more — otherwise a baseline's invented or
            # mis-attributed edges survive every update that leaves their methods alone.
            self.prune_relations(
                scope,
                self.reference_resolver.keep_relation_edge,
                changed_members or set(),
            )
            rels = scope.components_relations
        return rels

    @trace
    def generate_all_scope_relations(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        relation_contexts: dict[str, ScopeRelationContext],
        changed_members: set[str] | None = None,
        unattributed_files: Collection[str] = (),
    ) -> None:
        """Regenerate relations for every touched scope with at least two components.

        Why: scopes are independent (own clustering, cfg, analysis object), so they run
        concurrently; a single scope or none runs inline to skip worker setup.
        """
        tasks: list[tuple[str, AnalysisInsights]] = []
        if relation_contexts.get(ROOT_SCOPE_ID) is not None:
            tasks.append((ROOT_SCOPE_ID, root_analysis))
        for scope_id in sorted(relation_contexts.keys() - {ROOT_SCOPE_ID}):
            sub = sub_analyses.get(scope_id)
            if sub is not None:
                tasks.append((scope_id, sub))

        if len(tasks) <= 1:
            results = [
                (
                    scope_id,
                    self.generate_scope_relations(
                        scope,
                        scope_id,
                        relation_contexts[scope_id],
                        changed_members,
                        unattributed_files,
                    ),
                )
                for scope_id, scope in tasks
            ]
        else:
            results = self._generate_scope_relations_parallel(
                tasks,
                relation_contexts,
                changed_members,
                unattributed_files,
            )

        all_llm_rels = [
            (scope_id, rels) for scope_id, rels in results if rels and relation_contexts[scope_id].changed_ids
        ]

        if all_llm_rels:
            _log_scope_relations_summary(all_llm_rels)

    def _generate_scope_relations_parallel(
        self,
        tasks: list[tuple[str, AnalysisInsights]],
        relation_contexts: dict[str, ScopeRelationContext],
        changed_members: set[str] | None,
        unattributed_files: Collection[str],
    ) -> list[tuple[str, list[Relation]]]:
        """Regenerate each scope's relations concurrently, one agent clone per worker thread.

        Why: step_relation_analysis writes clustering onto the agent's shared toolkit.context, so
        scopes must not share an agent. executor.map preserves order for a deterministic log.
        """
        max_workers = min(len(tasks), os.cpu_count() or 4, 8)
        worker_local = threading.local()
        workers: list[IncrementalAgent] = []
        workers_lock = threading.Lock()

        def run_one(task: tuple[str, AnalysisInsights]) -> tuple[str, list[Relation]]:
            scope_id, scope = task
            worker = getattr(worker_local, "agent", None)
            if worker is None:
                worker = self._clone_for_worker()
                worker_local.agent = worker
                with workers_lock:
                    workers.append(worker)
            return scope_id, worker.generate_scope_relations(
                scope,
                scope_id,
                relation_contexts[scope_id],
                changed_members,
                unattributed_files,
            )

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(run_one, tasks))
        finally:
            # Merge even if a scope raised, so a failed run still reports the token/tool
            # usage the workers already incurred.
            for worker in workers:
                self.agent_stats.merge(worker.agent_stats)
        return results

    def _clone_for_worker(self) -> "IncrementalAgent":
        """A sibling agent with its own toolkit context, for concurrent scope regeneration."""
        return IncrementalAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.static_analysis,
            project_name=self.project_name,
            meta_context=self.meta_context,
            agent_llm=self.agent_llm,
            parsing_llm=self.parsing_llm,
            component_ownership=self.component_ownership,
            changes=self.toolkit.context.changes,
        )


def _new_component_membership_summary(component: Component) -> str:
    files = sorted(group.file_path for group in component.file_methods)
    symbols = sorted(
        {method.qualified_name for group in component.file_methods for method in group.methods},
        key=lambda qualified_name: (qualified_name.count("."), qualified_name),
    )
    shown_files = ", ".join(files[:8]) + (", ..." if len(files) > 8 else "")
    shown_symbols = ", ".join(symbols[:12]) + (", ..." if len(symbols) > 12 else "")
    return (
        f"Final membership: {len(symbols)} symbols across {len(files)} files. "
        f"Files: {shown_files}. Representative symbols: {shown_symbols}."
    )


def _log_scope_relations_summary(all_rels: list[tuple[str, list[Relation]]]) -> None:
    lines = ["[scope_relations] LLM-generated inter-component relations:"]
    for scope_name, rels in all_rels:
        for relation in rels:
            lines.append(f"  {scope_name:8s}  {relation.src_name:40s} --{relation.relation}--> {relation.dst_name}")
    logger.info("\n".join(lines))


def _operation_source_cluster_ids(scope_id: str, operation: ScopeOperation) -> list[str]:
    # A blank scope on a ref means root, matching how refs are read elsewhere.
    local_ids = {ref.cluster_id for ref in operation.cluster_refs if (ref.scope_id or ROOT_SCOPE_ID) == scope_id}
    return CodeBoardingClusterIds.qualify_local_ids(
        CodeBoardingClusterIds.from_graph_ids(local_ids),
        CodeBoardingClusterIds.prefix_for_scope(scope_id),
    )


def _remove_reassigned_clusters(
    scope_id: str,
    components: list[Component],
    components_by_id: dict[str, Component],
    decision: ScopeUpdateDecision,
) -> set[str]:
    reassigned_cluster_ids: set[str] = set()
    for operation in decision.operations:
        if operation.action == ScopeOperationAction.CREATE_COMPONENT or (
            operation.action == ScopeOperationAction.UPDATE_COMPONENT and operation.component_id in components_by_id
        ):
            reassigned_cluster_ids.update(_operation_source_cluster_ids(scope_id, operation))
    if not reassigned_cluster_ids:
        return set()

    changed_component_ids: set[str] = set()
    for component in components:
        kept_cluster_ids = [
            cluster_id for cluster_id in component.source_cluster_ids if cluster_id not in reassigned_cluster_ids
        ]
        if kept_cluster_ids == component.source_cluster_ids:
            continue
        component.source_cluster_ids = kept_cluster_ids
        if component.component_id:
            changed_component_ids.add(component.component_id)
    return changed_component_ids


def _log_duplicate_cluster_ownership(scope_id: str, components: list[Component]) -> None:
    owners_by_cluster_id: dict[str, list[str]] = {}
    for component in components:
        owner = component.component_id or component.name
        for cluster_id in component.source_cluster_ids:
            owners_by_cluster_id.setdefault(cluster_id, []).append(owner)

    duplicates = {cluster_id: owners for cluster_id, owners in owners_by_cluster_id.items() if len(owners) > 1}
    if duplicates:
        logger.error(
            "[incremental] duplicate cluster ownership remains in scope %s: %s",
            scope_id or "root",
            duplicates,
        )


def _component_id_parent(scope_id: str) -> str:
    return "" if scope_id == ROOT_SCOPE_ID else scope_id


def _patch_file_methods(
    scope: AnalysisInsights,
    patched_groups: dict[str, list[FileMethodGroup]],
    touched_ids: set[str],
    live_qnames: set[str],
) -> None:
    represented_qnames: set[str] = set()
    represented_physical_keys: set[tuple[str, int, int, str, str]] = set()
    for groups in patched_groups.values():
        for group in groups:
            for method in group.methods:
                represented_qnames.add(method.qualified_name)
                represented_physical_keys.add(_method_physical_key(group.file_path, method))

    stale_qnames: set[str] = set()
    stale_physical_keys: set[tuple[str, int, int, str, str]] = set()
    for component in scope.components:
        if component.component_id not in touched_ids:
            continue
        for group in component.file_methods:
            for method in group.methods:
                if method.qualified_name not in live_qnames:
                    stale_qnames.add(method.qualified_name)
                    stale_physical_keys.add(_method_physical_key(group.file_path, method))

    if represented_qnames or represented_physical_keys:
        for component in scope.components:
            component.file_methods = _without_methods(
                component.file_methods,
                represented_qnames,
                represented_physical_keys,
            )
    if stale_qnames or stale_physical_keys:
        for component in scope.components:
            if component.component_id not in touched_ids:
                continue
            component.file_methods = _without_methods(
                component.file_methods,
                stale_qnames,
                stale_physical_keys,
            )

    components_by_id = {component.component_id: component for component in scope.components if component.component_id}
    for component_id, groups in patched_groups.items():
        component = components_by_id.get(component_id)
        if component is None:
            continue
        component.file_methods = _merge_file_method_groups(component.file_methods, groups)


def _without_methods(
    groups: list[FileMethodGroup],
    qnames: set[str],
    physical_keys: set[tuple[str, int, int, str, str]],
) -> list[FileMethodGroup]:
    kept_groups: list[FileMethodGroup] = []
    for group in groups:
        kept_methods = [
            method
            for method in group.methods
            if method.qualified_name not in qnames
            and _method_physical_key(group.file_path, method) not in physical_keys
        ]
        if kept_methods:
            kept_groups.append(FileMethodGroup(file_path=group.file_path, methods=kept_methods))
    return kept_groups


def _merge_file_method_groups(
    existing_groups: list[FileMethodGroup],
    new_groups: list[FileMethodGroup],
) -> list[FileMethodGroup]:
    by_file: dict[str, dict[str, MethodEntry]] = {}
    for group in [*existing_groups, *new_groups]:
        methods = by_file.setdefault(group.file_path, {})
        for method in group.methods:
            methods[method.qualified_name] = method

    merged: list[FileMethodGroup] = []
    for file_path in sorted(by_file):
        merged.append(
            FileMethodGroup(
                file_path=file_path,
                methods=sorted(
                    by_file[file_path].values(),
                    key=lambda method: (method.start_line, method.end_line, method.qualified_name),
                ),
            )
        )
    return merged


def _method_physical_key(file_path: str, method: MethodEntry) -> tuple[str, int, int, str, str]:
    leaf_name = method.qualified_name.split(".")[-1]
    return (file_path, method.start_line, method.end_line, method.node_type, leaf_name)


def _live_cfg_qnames(static_analysis: StaticAnalysisResults) -> set[str]:
    qnames: set[str] = set()
    for language in static_analysis.get_languages():
        try:
            qnames.update(static_analysis.get_cfg(language).nodes)
        except (KeyError, ValueError):
            continue
    return qnames


def _component_has_live_cfg_methods(component: Component, live_qnames: set[str]) -> bool:
    return any(
        method.qualified_name in live_qnames for group in component.file_methods for method in group.methods
    ) or any(entity.qualified_name in live_qnames for entity in component.key_entities if entity.qualified_name)


def remove_deleted_files(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    live_files: set[str],
) -> set[str]:
    dropped_files: set[str] = _scrub_one_analysis(root_analysis, live_files)
    for sub in sub_analyses.values():
        dropped_files |= _scrub_one_analysis(sub, live_files)
    if dropped_files:
        logger.info("[incremental] dropped %d deleted file(s)", len(dropped_files))
    return dropped_files


def _scrub_one_analysis(analysis: AnalysisInsights, live_files: set[str]) -> set[str]:
    """Drop dead-file references in one analysis and return dropped paths."""
    dropped: set[str] = set()
    for component in analysis.components:
        had_methods = any(group.methods for group in component.file_methods)
        kept_groups = []
        for group in component.file_methods:
            if group.file_path in live_files:
                kept_groups.append(group)
            else:
                dropped.add(group.file_path)
        component.file_methods = kept_groups
        component.key_entities = [
            key_entity
            for key_entity in component.key_entities
            if key_entity.reference_file is None or key_entity.reference_file in live_files
        ]
        if had_methods and not any(group.methods for group in component.file_methods):
            component.source_cluster_ids = []
    dropped |= {file_path for file_path in analysis.files if file_path not in live_files}
    analysis.files = {file_path: entry for file_path, entry in analysis.files.items() if file_path in live_files}
    return dropped


def prune_empty_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> set[str]:
    """Remove components with neither members nor live cluster lineage."""
    removed_ids: set[str] = set()

    def has_methods(component: Component) -> bool:
        return (
            any(group.methods for group in component.file_methods)
            or bool(component.key_entities)
            or bool(component.source_cluster_ids)
        )

    def collect_empty(analysis: AnalysisInsights) -> None:
        for component in analysis.components:
            if component.component_id and not has_methods(component):
                removed_ids.add(component.component_id)

    collect_empty(root_analysis)
    for sub in sub_analyses.values():
        collect_empty(sub)
    _collect_descendant_ids(root_analysis, sub_analyses, removed_ids)

    if not removed_ids:
        return set()

    root_analysis.components = [
        component for component in root_analysis.components if component.component_id not in removed_ids
    ]
    _strip_relations(root_analysis, removed_ids)
    for sub in sub_analyses.values():
        sub.components = [component for component in sub.components if component.component_id not in removed_ids]
        _strip_relations(sub, removed_ids)
    for component_id in list(sub_analyses.keys()):
        if component_id in removed_ids:
            del sub_analyses[component_id]
    return removed_ids


def _collect_descendant_ids(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    removed_ids: set[str],
) -> None:
    if not removed_ids:
        return
    all_component_ids = {
        component.component_id for component in iter_components(root_analysis, sub_analyses) if component.component_id
    }
    all_component_ids.update(sub_analyses.keys())
    changed = True
    while changed:
        changed = False
        for component_id in all_component_ids - removed_ids:
            if any(component_id.startswith(f"{removed_id}.") for removed_id in removed_ids):
                removed_ids.add(component_id)
                changed = True


def _strip_relations(analysis: AnalysisInsights, removed_ids: set[str]) -> None:
    analysis.components_relations = [
        relation
        for relation in analysis.components_relations
        if relation.src_id not in removed_ids and relation.dst_id not in removed_ids
    ]
