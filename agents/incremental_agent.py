"""Incremental refresh helpers for scoped structural updates."""

import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    ComponentApiSurfaces,
    ComponentArchitecture,
    ComponentRelations,
    SourceCodeReference,
    MetaAnalysisInsights,
    Relation,
    ScopeOperation,
    ScopeOperationAction,
    ScopeUpdateDecision,
    assign_component_ids,
    assign_relation_ids,
    iter_components,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.content_hash import SourceCache
from agents.cluster_ids import CodeBoardingClusterIds
from agents.incremental_results import ScopeUpdateResult
from agents.prompts import get_api_surfaces_message, get_relation_analysis_message, get_system_message
from agents.scope_ids import ROOT_SCOPE_ID
from agents.validation import ValidationContext, validate_relations
from diagram_analysis.exceptions import IncrementalScopeContextMissingError, IncrementalScopeRegenerationRequiredError
from monitoring import trace
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language
from static_analyzer.graph import CallGraph, ClusterResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ScopeRelationContext:
    cluster_results: dict[str, ClusterResult]
    cfg_graphs: dict[str, CallGraph]


class IncrementalAgent(ClusterMethodsMixin, CodeBoardingAgent):
    """Materialize incremental plans and regenerate touched scope relations."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights | None,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        changes: ChangeSet | None = None,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message(), agent_llm, parsing_llm)
        if changes is not None:
            self.toolkit.context.changes = changes
        self.project_name = project_name
        self.meta_context = meta_context
        self._scope_relation_contexts: dict[str, _ScopeRelationContext] = {}
        self.prompts = {
            "api_surfaces": PromptTemplate(
                template=get_api_surfaces_message(),
                input_variables=[
                    "project_name",
                    "component_summaries",
                    "static_call_evidence",
                    "meta_context",
                    "project_type",
                ],
            ),
            "relation_analysis": PromptTemplate(
                template=get_relation_analysis_message(),
                input_variables=[
                    "project_name",
                    "component_summaries",
                    "api_surfaces",
                    "static_call_evidence",
                    "meta_context",
                    "project_type",
                ],
            ),
        }

    @trace
    def update_scope(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        decision: ScopeUpdateDecision,
        cluster_results: dict[str, ClusterResult],
    ) -> ScopeUpdateResult:
        """Apply a planning decision to one scope and refresh its derived fields."""
        result = ScopeUpdateResult()
        components_by_id = {
            component.component_id: component for component in scope.components if component.component_id
        }
        result.refresh_ids.update(_remove_reassigned_clusters(scope_id, scope.components, components_by_id, decision))

        for operation in decision.operations:
            if operation.action == ScopeOperationAction.REGENERATE_SCOPE:
                raise IncrementalScopeRegenerationRequiredError(scope_id, operation.rationale)
            if operation.action == ScopeOperationAction.CREATE_COMPONENT:
                self._create_component_from_operation(scope_id, scope, operation, components_by_id, result)
                continue
            if operation.action == ScopeOperationAction.DELETE_COMPONENT:
                if operation.component_id:
                    component = components_by_id.get(operation.component_id)
                    if component is not None and _component_has_live_cfg_methods(
                        component, _live_cfg_qnames(self.static_analysis)
                    ):
                        result.refresh_ids.add(operation.component_id)
                        continue
                    result.removed_ids.add(operation.component_id)
                continue
            if operation.action == ScopeOperationAction.NOOP:
                component = components_by_id.get(operation.component_id or "")
                if component is not None:
                    component.source_cluster_ids = CodeBoardingClusterIds.sort(
                        set(component.source_cluster_ids) | set(_operation_source_cluster_ids(scope_id, operation))
                    )
                    if component.component_id:
                        result.refresh_ids.add(component.component_id)
                continue

            component = components_by_id.get(operation.component_id or "")
            if component is None:
                continue
            self._update_component_from_operation(scope_id, component, operation)
            if component.component_id:
                result.refresh_ids.add(component.component_id)

        if result.removed_ids:
            scope.components = [
                component for component in scope.components if component.component_id not in result.removed_ids
            ]
            _strip_relations(scope, result.removed_ids)

        touched_ids = result.refresh_ids | result.new_component_ids
        if touched_ids:
            cfg_graphs = _cfg_graphs_for_cluster_results(self.static_analysis, cluster_results)
            self._patch_scope_file_methods(scope, cluster_results, cfg_graphs, touched_ids, scope_id)
            self._refresh_key_entities(scope, touched_ids)

        if touched_ids or result.removed_ids:
            self._scope_contexts()[scope_id] = _ScopeRelationContext(
                cluster_results=cluster_results,
                cfg_graphs=_cfg_graphs_for_scope_methods(self.static_analysis, scope),
            )

        _log_duplicate_cluster_ownership(scope_id, scope.components)

        return result

    def _create_component_from_operation(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        operation: ScopeOperation,
        components_by_id: dict[str, Component],
        result: ScopeUpdateResult,
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
            result.refresh_ids.add(component.component_id)
            result.new_component_ids.add(component.component_id)
            components_by_id[component.component_id] = component

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
        merged_cluster_ids = set(component.source_cluster_ids) | set(_operation_source_cluster_ids(scope_id, operation))
        component.source_cluster_ids = CodeBoardingClusterIds.sort(merged_cluster_ids)

    def _refresh_key_entities(self, scope: AnalysisInsights, component_ids: set[str]) -> None:
        for component in scope.components:
            if component.component_id not in component_ids:
                continue
            owned_qnames = {
                method.qualified_name
                for group in component.file_methods
                for method in group.methods
                if method.qualified_name
            }
            component.key_entities = [
                entity for entity in component.key_entities if entity.qualified_name in owned_qnames
            ][:5]
            if not component.key_entities:
                component.key_entities = _key_entities_from_file_methods(component)

    def _patch_scope_file_methods(
        self,
        scope: AnalysisInsights,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, CallGraph],
        touched_ids: set[str],
        scope_id: str,
    ) -> None:
        all_nodes = self._collect_all_cfg_nodes(cluster_results, cfg_graphs)
        cluster_to_component = self._build_cluster_to_component_map(scope)
        cluster_id_prefix = _cluster_id_prefix(scope_id)
        node_to_cluster, all_cluster_ids = self._build_node_to_cluster_map(cluster_results, cluster_id_prefix)
        self._validate_cluster_coverage(cluster_to_component, all_cluster_ids)

        component_nodes = self._assign_nodes_to_components(
            all_nodes,
            node_to_cluster,
            cluster_to_component,
            cluster_results,
            scope.components[0],
            cfg_graphs,
            cluster_id_prefix,
        )
        source_cache: SourceCache = {}
        patched_groups = {
            component_id: self._build_file_methods_from_nodes(nodes, source_cache)
            for component_id, nodes in component_nodes.items()
        }
        _patch_file_methods(scope, patched_groups, touched_ids, _live_cfg_qnames(self.static_analysis))
        scope.files = self.build_files_index(scope, source_cache)

    @trace
    def step_api_surfaces(self, scope: AnalysisInsights, scope_name: str) -> ComponentApiSurfaces:
        """Analyze API surfaces for one updated scope."""
        logger.info("[IncrementalAgent] Analyzing API surfaces for scope: %s", scope_name)
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"
        prompt = self.prompts["api_surfaces"].format(
            project_name=self.project_name,
            component_summaries=ComponentArchitecture(
                description=scope.description, components=scope.components
            ).llm_str(),
            static_call_evidence=self.build_scope_cfg_string(scope),
            meta_context=meta_context_str,
            project_type=project_type,
        )
        return self._validation_invoke(
            prompt,
            ComponentApiSurfaces,
            validators=[],
            context=None,
            max_validation_attempts=1,
        )

    @trace
    def step_relation_analysis(
        self,
        scope: AnalysisInsights,
        scope_name: str,
        api_surfaces: ComponentApiSurfaces,
        cluster_analysis: ClusterAnalysis,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, CallGraph],
    ) -> list[Relation]:
        """Discover evidence-backed relations and attach deterministic CFG edges."""
        logger.info("[IncrementalAgent] Discovering component relations for scope: %s", scope_name)
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"
        self.toolkit.context.cluster_analysis = cluster_analysis
        self.toolkit.context.cluster_results = cluster_results
        self.toolkit.context.cfg_graphs = cfg_graphs
        prompt = self.prompts["relation_analysis"].format(
            project_name=self.project_name,
            component_summaries=ComponentArchitecture(
                description=scope.description, components=scope.components
            ).llm_str(),
            api_surfaces=api_surfaces.llm_str(),
            static_call_evidence=self.build_scope_cfg_string(scope),
            meta_context=meta_context_str,
            project_type=project_type,
        )
        relation_result: ComponentRelations = self._validation_invoke(
            prompt,
            ComponentRelations,
            validators=[validate_relations],
            context=ValidationContext(
                cluster_results=cluster_results,
                cfg_graphs=cfg_graphs,
                repo_dir=str(self.repo_dir),
                static_analysis=self.static_analysis,
                llm_cluster_analysis=cluster_analysis,
                components=scope.components,
            ),
            max_validation_attempts=3,
        )
        scope.components_relations = relation_result.components_relations
        assign_relation_ids(scope)
        self.build_static_relations(scope, cfg_graphs)
        self.reference_resolver.fix_source_code_reference_lines(scope)
        return relation_result.components_relations

    @trace
    def generate_scope_relations(
        self,
        scope: AnalysisInsights,
        scope_name: str,
        cluster_results: dict[str, ClusterResult] | None = None,
        cfg_graphs: dict[str, CallGraph] | None = None,
    ) -> list[Relation]:
        """Run the API-surface and relation stages for one updated scope."""
        if len(scope.components) < 2:
            scope.components_relations = []
            self.reference_resolver.fix_source_code_reference_lines(scope)
            return []

        if cluster_results is None or cfg_graphs is None:
            context = self._scope_contexts().get(scope_name)
            if context is None:
                raise IncrementalScopeContextMissingError(scope_name)
            cluster_results = context.cluster_results
            cfg_graphs = context.cfg_graphs

        cluster_analysis = _cluster_analysis_for_scope(scope, scope_name, cluster_results)
        api_surfaces = self.step_api_surfaces(scope, scope_name)
        return self.step_relation_analysis(
            scope,
            scope_name,
            api_surfaces,
            cluster_analysis,
            cluster_results,
            cfg_graphs,
        )

    @trace
    def generate_all_scope_relations(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        touched_scopes: set[str],
    ) -> None:
        """Generate LLM relations for every touched scope with at least two components."""
        all_llm_rels: list[tuple[str, list[Relation]]] = []
        if ROOT_SCOPE_ID in touched_scopes:
            rels = self.generate_scope_relations(root_analysis, ROOT_SCOPE_ID)
            if rels:
                all_llm_rels.append((ROOT_SCOPE_ID, rels))
        for scope_id in sorted(touched_scopes - {ROOT_SCOPE_ID}):
            sub = sub_analyses.get(scope_id)
            if sub is not None:
                rels = self.generate_scope_relations(sub, scope_id)
                if rels:
                    all_llm_rels.append((scope_id, rels))

        if all_llm_rels:
            _log_scope_relations_summary(all_llm_rels)

    def _scope_contexts(self) -> dict[str, _ScopeRelationContext]:
        contexts = getattr(self, "_scope_relation_contexts", None)
        if contexts is None:
            contexts = {}
            self._scope_relation_contexts = contexts
        return contexts


def _cluster_analysis_for_scope(
    scope: AnalysisInsights,
    scope_id: str,
    cluster_results: dict[str, ClusterResult],
) -> ClusterAnalysis:
    """Reconstruct grouped-cluster context for a persisted incremental scope."""
    valid_cluster_ids = {
        cluster_id for cluster_result in cluster_results.values() for cluster_id in cluster_result.clusters
    }
    groups: list[ClustersComponent] = []
    for component in scope.components:
        cluster_ids = _local_graph_cluster_ids(component.source_cluster_ids, scope_id, valid_cluster_ids)
        if not component.source_group_names:
            component.source_group_names = [component.name]
        for group_name in component.source_group_names:
            groups.append(
                ClustersComponent(
                    name=group_name,
                    cluster_ids=cluster_ids,
                    description=component.description,
                )
            )
    return ClusterAnalysis(cluster_components=groups)


def _local_graph_cluster_ids(
    source_cluster_ids: list[str],
    scope_id: str,
    valid_cluster_ids: set[int],
) -> list[int]:
    prefix = "" if scope_id == ROOT_SCOPE_ID else f"{scope_id}."
    local_ids: set[int] = set()
    for source_cluster_id in source_cluster_ids:
        if prefix:
            if not source_cluster_id.startswith(prefix):
                continue
            local_id = source_cluster_id.removeprefix(prefix)
        else:
            local_id = source_cluster_id
        if local_id.isdigit() and int(local_id) in valid_cluster_ids:
            local_ids.add(int(local_id))
    return sorted(local_ids)


def _log_scope_relations_summary(all_rels: list[tuple[str, list[Relation]]]) -> None:
    lines = ["[scope_relations] LLM-generated inter-component relations:"]
    for scope_name, rels in all_rels:
        for relation in rels:
            lines.append(f"  {scope_name:8s}  {relation.src_name:40s} --{relation.relation}--> {relation.dst_name}")
    logger.info("\n".join(lines))


def _operation_source_cluster_ids(scope_id: str, operation: ScopeOperation) -> list[str]:
    local_ids = {ref.cluster_id for ref in operation.cluster_refs if ref.scope_id == scope_id}
    return CodeBoardingClusterIds.qualify_local_ids(
        CodeBoardingClusterIds.from_graph_ids(local_ids),
        _cluster_id_prefix(scope_id),
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


def _cluster_id_prefix(scope_id: str) -> str:
    return "" if scope_id == ROOT_SCOPE_ID else scope_id


def _component_id_parent(scope_id: str) -> str:
    return "" if scope_id == ROOT_SCOPE_ID else scope_id


def _key_entities_from_file_methods(component: Component) -> list[SourceCodeReference]:
    refs: list[SourceCodeReference] = []
    for group in sorted(component.file_methods, key=lambda file_group: file_group.file_path):
        for method in sorted(group.methods, key=lambda item: (item.start_line, item.end_line, item.qualified_name)):
            refs.append(
                SourceCodeReference(
                    qualified_name=method.qualified_name,
                    reference_file=group.file_path,
                    reference_start_line=method.start_line,
                    reference_end_line=method.end_line,
                )
            )
            if len(refs) == 5:
                return refs
    return refs


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


def _cfg_graphs_for_scope_methods(
    static_analysis: StaticAnalysisResults,
    scope: AnalysisInsights,
) -> dict[str, CallGraph]:
    scope_qnames = {
        method.qualified_name
        for component in scope.components
        for group in component.file_methods
        for method in group.methods
    }
    cfg_graphs: dict[str, CallGraph] = {}
    if not scope_qnames:
        return cfg_graphs
    for language in static_analysis.get_languages():
        try:
            cfg_graphs[str(language)] = static_analysis.get_cfg(language).filter_by_nodes(scope_qnames)
        except (KeyError, ValueError):
            continue
    return cfg_graphs


def _cfg_graphs_for_cluster_results(
    static_analysis: StaticAnalysisResults, cluster_results: dict[str, ClusterResult]
) -> dict[str, CallGraph]:
    cfg_graphs: dict[str, CallGraph] = {}
    for language, cluster_result in cluster_results.items():
        members = {qname for cluster_members in cluster_result.clusters.values() for qname in cluster_members}
        cfg_graphs[language] = static_analysis.get_cfg(Language(language)).filter_by_nodes(members)
    return cfg_graphs


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
    dropped |= {file_path for file_path in analysis.files if file_path not in live_files}
    analysis.files = {file_path: entry for file_path, entry in analysis.files.items() if file_path in live_files}
    return dropped


def prune_empty_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    protected_empty_ids: set[str] | None = None,
) -> set[str]:
    """Remove components with no methods and strip relations pointing to them."""
    removed_ids: set[str] = set()
    protected_empty_ids = protected_empty_ids or set()

    def has_methods(component: Component) -> bool:
        return (
            any(group.methods for group in component.file_methods)
            or bool(component.key_entities)
            or bool(component.component_id in protected_empty_ids and component.source_cluster_ids)
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
