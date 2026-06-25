from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.scoped_incremental_apply import apply_scope_update_decision


def test_apply_assign_to_existing_updates_description_and_clusters() -> None:
    component = Component(
        name="API",
        description="Old",
        key_entities=[],
        component_id="1",
        source_cluster_ids=["1"],
    )
    scope = AnalysisInsights(description="root", components=[component], components_relations=[])
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.UPDATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=2)],
                component_id="1",
                description="New",
                rationale="API gained a cluster.",
            )
        ]
    )

    result = apply_scope_update_decision("", scope, decision)

    assert component.description == "New"
    assert component.source_cluster_ids == ["1", "2"]
    assert result.refresh_ids == {"1"}
    assert result.new_component_ids == set()


def test_apply_create_component_assigns_root_id_and_clusters() -> None:
    existing = Component(name="API", description="", key_entities=[], component_id="1")
    scope = AnalysisInsights(description="root", components=[existing], components_relations=[])
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=7)],
                name="Worker",
                description="Runs jobs.",
                rationale="New isolated responsibility.",
            )
        ]
    )

    result = apply_scope_update_decision("", scope, decision)

    created = scope.components[1]
    assert created.component_id == "2"
    assert created.name == "Worker"
    assert created.source_cluster_ids == ["7"]
    assert result.refresh_ids == {"2"}
    assert result.new_component_ids == {"2"}


def test_apply_create_component_assigns_nested_id_and_qualified_cluster() -> None:
    existing = Component(name="Leaf", description="", key_entities=[], component_id="1.3.1")
    scope = AnalysisInsights(description="sub", components=[existing], components_relations=[])
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.CREATE_COMPONENT,
                cluster_refs=[ScopedClusterRef(scope_id="1.3", language="python", cluster_id=8)],
                name="Nested Worker",
                description="Runs nested jobs.",
                rationale="New nested responsibility.",
            )
        ]
    )

    result = apply_scope_update_decision("1.3", scope, decision)

    created = scope.components[1]
    assert created.component_id == "1.3.2"
    assert created.source_cluster_ids == ["1.3.8"]
    assert result.new_component_ids == {"1.3.2"}


def test_apply_delete_component_removes_relations() -> None:
    first = Component(name="A", description="", key_entities=[], component_id="1")
    second = Component(name="B", description="", key_entities=[], component_id="2")
    relation = Relation(relation="calls", src_name="A", dst_name="B", src_id="1", dst_id="2")
    scope = AnalysisInsights(description="root", components=[first, second], components_relations=[relation])
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.DELETE_COMPONENT,
                cluster_refs=[],
                component_id="1",
                rationale="Removed cluster emptied component.",
            )
        ]
    )

    result = apply_scope_update_decision("", scope, decision)

    assert [component.component_id for component in scope.components] == ["2"]
    assert scope.components_relations == []
    assert result.removed_ids == {"1"}


def test_apply_regenerate_scope_sets_flag_without_mutation() -> None:
    component = Component(name="A", description="", key_entities=[], component_id="1")
    scope = AnalysisInsights(description="root", components=[component], components_relations=[])
    decision = ScopeUpdateDecision(
        operations=[
            ScopeOperation(
                action=ScopeOperationAction.REGENERATE_SCOPE,
                cluster_refs=[],
                rationale="Ambiguous reparenting required.",
            )
        ]
    )

    result = apply_scope_update_decision("", scope, decision)

    assert result.regenerate_scope
    assert scope.components == [component]
