"""Apply scoped incremental operations to one analysis scope."""

from dataclasses import dataclass, field

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    ScopeOperation,
    ScopeOperationAction,
    ScopeUpdateDecision,
)
from agents.cluster_ids import CodeBoardingClusterIds


@dataclass
class ScopeApplyResult:
    refresh_ids: set[str] = field(default_factory=set)
    new_component_ids: set[str] = field(default_factory=set)
    removed_ids: set[str] = field(default_factory=set)
    regenerate_scope: bool = False


def apply_scope_update_decision(
    scope_id: str,
    scope: AnalysisInsights,
    decision: ScopeUpdateDecision,
) -> ScopeApplyResult:
    """Apply operation-model output to a single scope only."""
    result = ScopeApplyResult()
    components_by_id = {component.component_id: component for component in scope.components if component.component_id}

    for operation in decision.operations:
        if operation.action == ScopeOperationAction.REGENERATE_SCOPE:
            result.regenerate_scope = True
            continue
        if operation.action == ScopeOperationAction.CREATE_COMPONENT:
            component = _component_from_operation(scope_id, operation)
            scope.components.append(component)
            _assign_new_ids(scope_id, scope)
            if component.component_id:
                result.refresh_ids.add(component.component_id)
                result.new_component_ids.add(component.component_id)
                components_by_id[component.component_id] = component
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
        _apply_component_update(scope_id, component, operation)
        if component.component_id:
            result.refresh_ids.add(component.component_id)

    if result.removed_ids:
        scope.components = [
            component for component in scope.components if component.component_id not in result.removed_ids
        ]
        scope.components_relations = [
            relation
            for relation in scope.components_relations
            if relation.src_id not in result.removed_ids and relation.dst_id not in result.removed_ids
        ]
    return result


def _component_from_operation(scope_id: str, operation: ScopeOperation) -> Component:
    return Component(
        name=operation.name or "New Component",
        description=operation.description or "",
        key_entities=[],
        source_group_names=[operation.name or "New Component"],
        source_cluster_ids=_operation_source_cluster_ids(scope_id, operation),
    )


def _apply_component_update(scope_id: str, component: Component, operation: ScopeOperation) -> None:
    if operation.name:
        component.name = operation.name
    if operation.description:
        component.description = operation.description
    merged_cluster_ids = set(component.source_cluster_ids) | set(_operation_source_cluster_ids(scope_id, operation))
    component.source_cluster_ids = CodeBoardingClusterIds.sort(merged_cluster_ids)


def _operation_source_cluster_ids(scope_id: str, operation: ScopeOperation) -> list[str]:
    local_ids = {ref.cluster_id for ref in operation.cluster_refs if ref.scope_id == scope_id}
    return CodeBoardingClusterIds.qualify_local_ids(CodeBoardingClusterIds.from_graph_ids(local_ids), scope_id)


def _assign_new_ids(scope_id: str, scope: AnalysisInsights) -> None:
    if not any(not component.component_id for component in scope.components):
        return
    assign_parent_id = scope_id or ""
    next_idx = 1
    used = {component.component_id for component in scope.components if component.component_id}
    while _candidate_component_id(assign_parent_id, next_idx) in used:
        next_idx += 1
    for component in scope.components:
        if component.component_id:
            continue
        while True:
            candidate = _candidate_component_id(assign_parent_id, next_idx)
            next_idx += 1
            if candidate not in used:
                component.component_id = candidate
                used.add(candidate)
                break


def _candidate_component_id(parent_id: str, idx: int) -> str:
    return f"{parent_id}.{idx}" if parent_id else str(idx)
