"""Incremental refresh helpers for scoped structural updates."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
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
from agents.prompts import get_scope_relations_message, get_system_message
from agents.validation import ValidationContext, validate_scope_relation_names
from monitoring import trace
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_relations import merge_relations
from static_analyzer.constants import Language
from static_analyzer.graph import CallGraph, ClusterResult

logger = logging.getLogger(__name__)


@dataclass
class ScopeUpdateResult:
    """Result of applying one incremental plan to one analysis scope."""

    refresh_ids: set[str] = field(default_factory=set)
    new_component_ids: set[str] = field(default_factory=set)
    removed_ids: set[str] = field(default_factory=set)
    regenerate_scope: bool = False


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
            self.toolkit.context.diff_base_ref = changes.base_ref
            self.toolkit.context.diff_target_ref = changes.target_ref
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

        for operation in decision.operations:
            if operation.action == ScopeOperationAction.REGENERATE_SCOPE:
                result.regenerate_scope = True
                continue
            if operation.action == ScopeOperationAction.CREATE_COMPONENT:
                self._create_component_from_operation(scope_id, scope, operation, components_by_id, result)
                continue
            if operation.action == ScopeOperationAction.DELETE_COMPONENT:
                if operation.component_id:
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
            self.populate_file_methods(scope, cluster_results, cfg_graphs, source_cluster_id_prefix=scope_id)
            self.build_static_relations(scope, cfg_graphs)
            self._refresh_key_entities(scope, touched_ids)

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
        assign_component_ids(scope, parent_id=scope_id, only_new=True)
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
        merged = merge_relations(result.components_relations, [], scope)
        for relation in existing_static:
            existing_pair = next(
                (m for m in merged if m.src_id == relation.src_id and m.dst_id == relation.dst_id),
                None,
            )
            if existing_pair is None:
                merged.append(relation)
            elif existing_pair.edge_count == 0 and relation.edge_count > 0:
                existing_pair.edge_count = relation.edge_count
                existing_pair.is_static = True

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
    local_ids = {ref.cluster_id for ref in operation.cluster_refs if _normalize_scope_id(ref.scope_id) == scope_id}
    return CodeBoardingClusterIds.qualify_local_ids(CodeBoardingClusterIds.from_graph_ids(local_ids), scope_id)


def _normalize_scope_id(scope_id: str) -> str:
    return "" if scope_id == "root" else scope_id


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
) -> set[str]:
    """Remove components with no methods and strip relations pointing to them."""
    removed_ids: set[str] = set()

    def has_methods(component: Component) -> bool:
        return any(group.methods for group in component.file_methods)

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
