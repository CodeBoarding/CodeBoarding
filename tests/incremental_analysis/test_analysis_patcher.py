import json
from unittest.mock import MagicMock, patch

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from incremental_analysis.analysis_patcher import (
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


class _RelationIdEchoExtractor:
    def __init__(self):
        self.snapshot = None

    def invoke(self, state, config=None):
        prompt = state["messages"][0].content
        json_blob = prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        self.snapshot = json.loads(json_blob)

        component_ids_by_name = {
            component["name"]: component["component_id"] for component in self.snapshot["components"]
        }
        relations = []
        for relation in self.snapshot["relations"]:
            relations.append(
                {
                    "src_id": relation.get("src_id")
                    or component_ids_by_name.get(relation["src_name"], relation["src_name"]),
                    "dst_id": relation.get("dst_id")
                    or component_ids_by_name.get(relation["dst_name"], relation["dst_name"]),
                    "relation": f"patched:{relation['relation']}",
                    "src_name": relation["src_name"],
                    "dst_name": relation["dst_name"],
                }
            )

        return {
            "responses": [
                {
                    "scope_description": self.snapshot["description"],
                    "components": [],
                    "relations": relations,
                }
            ]
        }


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


def test_apply_scope_patch_falls_back_to_unique_relation_names_when_ids_miss():
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
                src_id="1.1",
                dst_id="Store",
                relation="depends_on",
                src_name="Auth",
                dst_name="Store",
            ),
            RelationPatch(
                src_id="Store",
                dst_id="1.1",
                relation="signals",
                src_name="Store",
                dst_name="Auth",
            ),
        ],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    relation_by_ids = {
        (relation.src_id, relation.dst_id): relation.relation for relation in patched.components_relations
    }
    assert relation_by_ids[("1.1", "1.2")] == "depends_on"
    assert relation_by_ids[("1.2", "1.1")] == "signals"


def test_apply_scope_patch_skips_name_fallback_when_relation_names_are_ambiguous():
    analysis = AnalysisInsights(
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
                description="Old primary store description",
                key_entities=[SourceCodeReference(qualified_name="store.primary")],
            ),
            Component(
                name="Store",
                component_id="1.3",
                description="Old replica store description",
                key_entities=[SourceCodeReference(qualified_name="store.replica")],
            ),
        ],
        components_relations=[
            Relation(relation="calls_primary", src_name="Auth", dst_name="Store", src_id="1.1", dst_id="1.2"),
            Relation(relation="calls_replica", src_name="Auth", dst_name="Store", src_id="1.1", dst_id="1.3"),
        ],
    )
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
                src_id="1.1",
                dst_id="Store",
                relation="depends_on",
                src_name="Auth",
                dst_name="Store",
            )
        ],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    relation_by_ids = {
        (relation.src_id, relation.dst_id): relation.relation for relation in patched.components_relations
    }
    assert relation_by_ids[("1.1", "1.2")] == "calls_primary"
    assert relation_by_ids[("1.1", "1.3")] == "calls_replica"


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
    with patch("incremental_analysis.analysis_patcher.create_extractor", return_value=extractor):
        patched = patch_analysis_scope(analysis, patch_scope, MagicMock())

    assert extractor.calls == 3
    assert patched is not None
    assert {component.component_id: component.description for component in patched.components}["1.1"] == (
        "Updated auth description"
    )


def test_patch_analysis_scope_includes_relation_ids_for_relations_touching_targeted_components():
    analysis = _make_analysis()
    analysis.components_relations.append(
        Relation(relation="reads", src_name="Auth", dst_name="Cache", src_id="1.1", dst_id="1.3")
    )
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )

    extractor = _RelationIdEchoExtractor()
    with patch("incremental_analysis.analysis_patcher.create_extractor", return_value=extractor):
        patched = patch_analysis_scope(analysis, patch_scope, MagicMock())

    assert patched is not None
    assert extractor.snapshot is not None
    assert all("src_id" in relation and "dst_id" in relation for relation in extractor.snapshot["relations"])
    relation_by_ids = {
        (relation.src_id, relation.dst_id): relation.relation for relation in patched.components_relations
    }
    assert relation_by_ids[("1.1", "1.2")] == "patched:calls"
    assert relation_by_ids[("1.1", "1.3")] == "patched:reads"
    assert relation_by_ids[("1.2", "1.1")] == "patched:emits"


def test_patch_analysis_scope_salvages_stringified_key_entities():
    analysis = _make_analysis()
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )

    class _Extractor:
        def invoke(self, _state, config=None):
            return {
                "responses": [
                    {
                        "scope_description": "Updated scope",
                        "components": [
                            {
                                "component_id": "1.1",
                                "description": "Updated auth description",
                                "key_entities": ['{"qualified_name": "auth.refresh"}'],
                            }
                        ],
                        "relations": [],
                    }
                ]
            }

    with patch("incremental_analysis.analysis_patcher.create_extractor", return_value=_Extractor()):
        patched = patch_analysis_scope(analysis, patch_scope, MagicMock())

    assert patched is not None
    component = {component.component_id: component for component in patched.components}["1.1"]
    assert component.key_entities[0].qualified_name == "auth.refresh"


def test_patch_analysis_scope_feeds_validation_error_back_into_retry():
    analysis = _make_analysis()
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )

    class _Extractor:
        def __init__(self):
            self.states = []

        def invoke(self, state, config=None):
            self.states.append(state)
            if len(self.states) == 1:
                return {
                    "responses": [
                        {
                            "scope_description": "Updated scope",
                            "components": [
                                {
                                    "component_id": "1.1",
                                    "description": "Updated auth description",
                                    "key_entities": ["not valid json"],
                                }
                            ],
                            "relations": [],
                        }
                    ]
                }
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
    with patch("incremental_analysis.analysis_patcher.create_extractor", return_value=extractor):
        patched = patch_analysis_scope(analysis, patch_scope, MagicMock())

    assert patched is not None
    assert len(extractor.states) == 2
    retry_messages = extractor.states[1]["messages"]
    assert len(retry_messages) == 2
    assert "failed validation" in retry_messages[-1].content
    assert "components.0.key_entities.0" in retry_messages[-1].content
