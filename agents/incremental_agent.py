"""Incremental refresh helpers for scoped structural updates."""

import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    MethodEntry,
    SourceCodeReference,
    MetaAnalysisInsights,
    Relation,
    ScopeOperation,
    ScopeOperationAction,
    ScopeRelations,
    ScopeUpdateDecision,
    assign_component_ids,
    iter_components,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.cluster_ids import CodeBoardingClusterIds
from agents.incremental_results import ScopeUpdateResult
from agents.prompts import get_scope_relations_message, get_system_message
from agents.scope_ids import ROOT_SCOPE_ID
from agents.validation import ValidationContext, validate_scope_relation_names
from monitoring import trace
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_relations import ClusterRelation, merge_relations
from static_analyzer.constants import Language
from static_analyzer.graph import CallGraph, ClusterResult

logger = logging.getLogger(__name__)


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
                result.regenerate_scope = True
                continue
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
            self.build_static_relations(scope, _cfg_graphs_for_scope_methods(self.static_analysis, scope))
            self._refresh_key_entities(scope, touched_ids)

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
            key_entities=[],
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
        merged_cluster_ids = set(component.source_cluster_ids) | set(_operation_source_cluster_ids(scope_id, operation))
        component.source_cluster_ids = CodeBoardingClusterIds.sort(merged_cluster_ids)

    def _refresh_key_entities(self, scope: AnalysisInsights, component_ids: set[str]) -> None:
        for component in scope.components:
            if component.component_id not in component_ids:
                continue
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
        patched_groups = {
            component_id: self._build_file_methods_from_nodes(nodes) for component_id, nodes in component_nodes.items()
        }
        _patch_file_methods(scope, patched_groups, touched_ids, _live_cfg_qnames(self.static_analysis))
        scope.files = self.build_files_index(scope)

    @trace
    def generate_scope_relations(self, scope: AnalysisInsights, scope_name: str) -> list[Relation]:
        """Generate LLM relations for a single scope and merge with static relations."""
        if len(scope.components) < 2:
            return []

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"
        component_summaries = "\n".join(c.llm_str() for c in scope.components)
        cross_calls = self.build_scope_cfg_string(scope)

        prompt_template = PromptTemplate(
            template=get_scope_relations_message(),
            input_variables=[
                "scope_name",
                "project_name",
                "meta_context",
                "project_type",
                "component_summaries",
                "cross_component_calls",
            ],
        )
        prompt = prompt_template.format(
            scope_name=scope_name,
            project_name=self.project_name,
            meta_context=meta_context_str,
            project_type=project_type,
            component_summaries=component_summaries,
            cross_component_calls=cross_calls,
        )

        valid_names = {c.name for c in scope.components}
        result: ScopeRelations = self._validation_invoke(
            prompt,
            ScopeRelations,
            validators=[validate_scope_relation_names],
            context=ValidationContext(valid_component_names=valid_names),
            max_validation_attempts=3,
        )

        existing_static = [r for r in scope.components_relations if r.is_static]
        static_relations = [
            ClusterRelation(
                src_cluster_id=relation.src_id,
                dst_cluster_id=relation.dst_id,
                edge_count=relation.edge_count,
                bridge_edges=relation.bridge_edges,
            )
            for relation in existing_static
        ]
        merged = merge_relations(result.components_relations, static_relations, scope)

        scope.components_relations = merged
        return result.components_relations

    @trace
    def generate_all_scope_relations(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        touched_scopes: set[str],
    ) -> None:
        """Generate LLM relations for every touched scope with at least two components."""
        all_llm_rels: list[tuple[str, list[Relation]]] = []
        if "" in touched_scopes:
            rels = self.generate_scope_relations(root_analysis, "root")
            if rels:
                all_llm_rels.append(("root", rels))
        for scope_id in sorted(touched_scopes - {""}):
            sub = sub_analyses.get(scope_id)
            if sub is not None:
                rels = self.generate_scope_relations(sub, scope_id)
                if rels:
                    all_llm_rels.append((scope_id, rels))

        if all_llm_rels:
            _log_scope_relations_summary(all_llm_rels)


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
