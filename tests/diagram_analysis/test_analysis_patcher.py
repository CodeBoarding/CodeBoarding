from unittest.mock import MagicMock, patch

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from diagram_analysis.analysis_patcher import (
    AnalysisScopePatch,
    ComponentPatch,
    PatchScope,
    RelationPatch,
    apply_scope_patch,
    patch_analysis_scope,
)


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
            Component(
                name="Cache",
                component_id="1.3",
                description="Old cache description",
                key_entities=[SourceCodeReference(qualified_name="cache.get")],
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
            ComponentPatch(
                component_id="1.1",
                description="Updated auth description",
                key_entities=[SourceCodeReference(qualified_name="auth.refresh")],
            )
        ],
        relations=[
            RelationPatch(
                src_id="1.1",
                dst_id="1.2",
                relation="depends_on",
                src_name="Auth",
                dst_name="Store",
            )
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


def test_apply_scope_patch_ignores_out_of_scope_relation_additions():
    analysis = _make_analysis()
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[],
        relations=[
            RelationPatch(
                src_id="1.2",
                dst_id="1.3",
                relation="feeds",
                src_name="Store",
                dst_name="Cache",
            )
        ],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    assert {("1.2", "1.3"), ("1.3", "1.2")} & {
        (relation.src_id, relation.dst_id) for relation in patched.components_relations
    } == set()


def test_patch_analysis_scope_retries_three_times_before_success():
    analysis = _make_analysis()
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )

    class _Extractor:
        def __init__(self):
            self.calls = 0

        def invoke(self, _prompt, config=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary failure")
            if self.calls == 2:
                return {"responses": []}
            return {
                "responses": [
                    {
                        "scope_description": "Updated scope",
                        "components": [
                            {
                                "component_id": "1.1",
                                "description": "Updated auth description",
                                "key_entities": [{"qualified_name": "auth.refresh"}],
                            }
                        ],
                        "relations": [],
                    }
                ]
            }

    extractor = _Extractor()
    with patch("diagram_analysis.analysis_patcher.create_extractor", return_value=extractor):
        patched = patch_analysis_scope(analysis, patch_scope, MagicMock())

    assert extractor.calls == 3
    assert patched is not None
    assert {component.component_id: component.description for component in patched.components}["1.1"] == (
        "Updated auth description"
    )
