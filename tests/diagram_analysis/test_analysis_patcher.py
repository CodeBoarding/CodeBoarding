from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from diagram_analysis.analysis_patcher import AnalysisScopePatch, PatchScope, apply_scope_patch


def _make_analysis() -> AnalysisInsights:
    return AnalysisInsights(
        description="Original scope",
        components=[
            Component(
                name="Auth",
                component_id="1.1",
                description="Old auth description",
                key_entities=[SourceCodeReference(qualified_name="auth.login")],
            ),
            Component(
                name="Store",
                component_id="1.2",
                description="Old store description",
                key_entities=[SourceCodeReference(qualified_name="store.save")],
            ),
        ],
        components_relations=[
            Relation(relation="calls", src_name="Auth", dst_name="Store", src_id="1.1", dst_id="1.2"),
            Relation(relation="emits", src_name="Store", dst_name="Auth", src_id="1.2", dst_id="1.1"),
        ],
    )


def test_apply_scope_patch_updates_only_targeted_components_and_relations():
    analysis = _make_analysis()
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )
    scope_patch = AnalysisScopePatch(
        scope_description="Updated scope",
        components=[
            {
                "component_id": "1.1",
                "description": "Updated auth description",
                "key_entities": [{"qualified_name": "auth.refresh"}],
            }
        ],
        relations=[
            {
                "src_id": "1.1",
                "dst_id": "1.2",
                "relation": "depends_on",
                "src_name": "Auth",
                "dst_name": "Store",
            }
        ],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    components = {component.component_id: component for component in patched.components}
    assert patched.description == "Updated scope"
    assert components["1.1"].description == "Updated auth description"
    assert components["1.1"].key_entities[0].qualified_name == "auth.refresh"
    assert components["1.2"].description == "Old store description"

    relation_by_ids = {
        (relation.src_id, relation.dst_id): relation.relation for relation in patched.components_relations
    }
    assert relation_by_ids[("1.1", "1.2")] == "depends_on"
    assert relation_by_ids[("1.2", "1.1")] == "emits"
