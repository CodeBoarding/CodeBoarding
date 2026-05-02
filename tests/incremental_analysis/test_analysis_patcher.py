import json
from unittest.mock import MagicMock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    Relation,
    SourceCodeReference,
)
from incremental_analysis.analysis_patcher import (
    AnalysisScopePatch,
    ComponentPatch,
    NewComponentSpec,
    PatchScope,
    RelationPatch,
    _assign_new_component_id,
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


def test_patch_analysis_scope_salvages_stringified_nested_objects():
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
                            json.dumps(
                                {
                                    "component_id": "1.1",
                                    "description": "Updated auth description",
                                    "key_entities": [json.dumps({"qualified_name": "auth.refresh"})],
                                }
                            )
                        ],
                        "relations": [
                            json.dumps(
                                {
                                    "src_id": "1.1",
                                    "dst_id": "1.2",
                                    "relation": "depends_on",
                                    "src_name": "Auth",
                                    "dst_name": "Store",
                                }
                            )
                        ],
                    }
                ]
            }

    with patch("incremental_analysis.analysis_patcher.create_extractor", return_value=_Extractor()):
        patched = patch_analysis_scope(analysis, patch_scope, MagicMock())

    assert patched is not None
    component = {component.component_id: component for component in patched.components}["1.1"]
    assert component.key_entities[0].qualified_name == "auth.refresh"
    relation_by_ids = {
        (relation.src_id, relation.dst_id): relation.relation for relation in patched.components_relations
    }
    assert relation_by_ids[("1.1", "1.2")] == "depends_on"


def test_patch_analysis_scope_salvages_mixed_valid_and_stringified_objects():
    analysis = _make_analysis()
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1", "1.2"],
        visited_methods=["auth.login", "store.save"],
        impacted_methods=["auth.login", "store.save"],
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
                                "key_entities": [{"qualified_name": "auth.refresh"}],
                            },
                            json.dumps(
                                {
                                    "component_id": "1.2",
                                    "description": "Updated store description",
                                    "key_entities": [json.dumps({"qualified_name": "store.flush"})],
                                }
                            ),
                        ],
                        "relations": [
                            {
                                "src_id": "1.1",
                                "dst_id": "1.2",
                                "relation": "depends_on",
                                "src_name": "Auth",
                                "dst_name": "Store",
                            },
                            json.dumps(
                                {
                                    "src_id": "1.2",
                                    "dst_id": "1.1",
                                    "relation": "signals",
                                    "src_name": "Store",
                                    "dst_name": "Auth",
                                }
                            ),
                        ],
                    }
                ]
            }

    with patch("incremental_analysis.analysis_patcher.create_extractor", return_value=_Extractor()):
        patched = patch_analysis_scope(analysis, patch_scope, MagicMock())

    assert patched is not None
    components = {component.component_id: component for component in patched.components}
    assert components["1.1"].description == "Updated auth description"
    assert components["1.2"].description == "Updated store description"
    assert components["1.2"].key_entities[0].qualified_name == "store.flush"
    relation_by_ids = {
        (relation.src_id, relation.dst_id): relation.relation for relation in patched.components_relations
    }
    assert relation_by_ids[("1.1", "1.2")] == "depends_on"
    assert relation_by_ids[("1.2", "1.1")] == "signals"


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
    assert "Every nested object in the payload must be an object" in retry_messages[-1].content
    assert "components.0.key_entities.0" in retry_messages[-1].content


def test_assign_new_component_id_top_level_namespace() -> None:
    assert _assign_new_component_id(None, set()) == "1"
    assert _assign_new_component_id(None, {"1", "2", "3"}) == "4"
    # Gaps are filled before extending.
    assert _assign_new_component_id(None, {"1", "3"}) == "2"
    # Grandchildren must not collapse the parent's namespace.
    assert _assign_new_component_id(None, {"1", "1.1"}) == "2"


def test_assign_new_component_id_sub_scope_namespace() -> None:
    assert _assign_new_component_id("1", set()) == "1.1"
    assert _assign_new_component_id("1", {"1.1", "1.2", "1.3"}) == "1.4"
    # Direct children only — grandchildren do not consume slots.
    assert _assign_new_component_id("1", {"1.1", "1.1.1", "1.2"}) == "1.3"
    assert _assign_new_component_id("1.1", {"1.1.1", "1.1.3"}) == "1.1.2"


def test_apply_scope_patch_mints_new_component_under_root_scope() -> None:
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
        new_components=[
            NewComponentSpec(
                name="Telemetry & Analytics",
                description="Buffers and posts runtime events.",
                key_entities=[
                    SourceCodeReference(qualified_name="telemetry.TelemetryService"),
                ],
            )
        ],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    minted = [c for c in patched.components if c.name == "Telemetry & Analytics"]
    assert len(minted) == 1
    # 1.1/1.2/1.3 have dots so they are not direct children of the root.
    # Next free top-level slot is "1".
    assert minted[0].component_id == "1"


def test_apply_scope_patch_mints_new_component_under_sub_scope() -> None:
    analysis = _make_analysis()  # has 1.1, 1.2, 1.3
    patch_scope = PatchScope(
        scope_id="1",
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[],
        new_components=[
            NewComponentSpec(
                name="Telemetry",
                description="New telemetry subsystem.",
                key_entities=[SourceCodeReference(qualified_name="telemetry.Service")],
            )
        ],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    minted = [c for c in patched.components if c.name == "Telemetry"]
    assert len(minted) == 1
    # scope_id="1" → next free direct child after 1.1, 1.2, 1.3 is 1.4.
    assert minted[0].component_id == "1.4"


def test_apply_scope_patch_skips_new_component_with_duplicate_name() -> None:
    analysis = _make_analysis()  # has Auth, Store, Cache
    patch_scope = PatchScope(
        scope_id="1",
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[],
        new_components=[
            NewComponentSpec(
                name="Auth",  # already exists
                description="Re-described auth.",
                key_entities=[],
            )
        ],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    # Only the original "Auth" remains; no minted duplicate.
    assert [c.name for c in patched.components if c.name == "Auth"] == ["Auth"]
    assert len(patched.components) == len(analysis.components)


def test_apply_scope_patch_relations_can_reference_minted_component_ids() -> None:
    analysis = _make_analysis()  # has 1.1, 1.2, 1.3
    patch_scope = PatchScope(
        scope_id="1",
        target_component_ids=["1.1"],
        visited_methods=["auth.login"],
        impacted_methods=["auth.login"],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[],
        new_components=[
            NewComponentSpec(
                name="Telemetry",
                description="New telemetry subsystem.",
                key_entities=[SourceCodeReference(qualified_name="telemetry.Service")],
            )
        ],
        relations=[
            RelationPatch(
                src_id="1.1",
                dst_id="1.4",  # the about-to-be-minted id (next free under scope_id="1")
                relation="emits telemetry to",
                src_name="Auth",
                dst_name="Telemetry",
            ),
        ],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    new_relations = [relation for relation in patched.components_relations if relation.dst_id == "1.4"]
    assert len(new_relations) == 1
    assert new_relations[0].relation == "emits telemetry to"
    assert new_relations[0].src_id == "1.1"


def test_apply_scope_patch_existing_patches_still_drop_new_component_id_in_components() -> None:
    """Backwards-compat: a `ComponentPatch` whose id isn't pre-existing is still dropped.

    This guards against accidental component creation via the patch path —
    creation now has its own field (`new_components`).
    """
    analysis = _make_analysis()
    patch_scope = PatchScope(
        scope_id=None,
        target_component_ids=["1.1"],
        visited_methods=[],
        impacted_methods=[],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[
            ComponentPatch(
                component_id="9.9",  # never existed
                description="ghost",
                key_entities=[],
            )
        ],
        new_components=[],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    assert [c.component_id for c in patched.components] == ["1.1", "1.2", "1.3"]


def _telemetry_method(qname: str) -> MethodEntry:
    return MethodEntry(qualified_name=qname, start_line=1, end_line=10, node_type="FUNCTION")


def _make_analysis_with_unallocated_files() -> AnalysisInsights:
    """Three existing components in scope `"1"`, plus 3 telemetry files in
    the global `files` index that no component owns."""
    return AnalysisInsights(
        description="Original scope",
        files={
            "src/auth.py": FileEntry(methods=[_telemetry_method("auth.login")]),
            "src/store.py": FileEntry(methods=[_telemetry_method("store.save")]),
            "src/cache.py": FileEntry(methods=[_telemetry_method("cache.get")]),
            "src/telemetry/event.py": FileEntry(methods=[_telemetry_method("telemetry.event.make")]),
            "src/telemetry/queue.py": FileEntry(methods=[_telemetry_method("telemetry.queue.enqueue")]),
            "src/telemetry/service.py": FileEntry(methods=[_telemetry_method("telemetry.service.flush")]),
        },
        components=[
            Component(
                name="Auth",
                component_id="1.1",
                description="auth",
                key_entities=[SourceCodeReference(qualified_name="auth.login")],
                file_methods=[FileMethodGroup(file_path="src/auth.py", methods=[_telemetry_method("auth.login")])],
            ),
            Component(
                name="Store",
                component_id="1.2",
                description="store",
                key_entities=[SourceCodeReference(qualified_name="store.save")],
                file_methods=[FileMethodGroup(file_path="src/store.py", methods=[_telemetry_method("store.save")])],
            ),
            Component(
                name="Cache",
                component_id="1.3",
                description="cache",
                key_entities=[SourceCodeReference(qualified_name="cache.get")],
                file_methods=[FileMethodGroup(file_path="src/cache.py", methods=[_telemetry_method("cache.get")])],
            ),
        ],
        components_relations=[],
    )


def test_apply_scope_patch_owned_files_materialize_file_methods_on_minted_component() -> None:
    analysis = _make_analysis_with_unallocated_files()
    patch_scope = PatchScope(
        scope_id="1",
        target_component_ids=["1.1", "1.2", "1.3"],
        visited_methods=[],
        impacted_methods=[],
        unallocated_files=[
            "src/telemetry/event.py",
            "src/telemetry/queue.py",
            "src/telemetry/service.py",
        ],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[],
        new_components=[
            NewComponentSpec(
                name="Telemetry",
                description="Buffers and posts runtime events.",
                key_entities=[SourceCodeReference(qualified_name="telemetry.service.flush")],
                owned_files=[
                    "src/telemetry/event.py",
                    "src/telemetry/queue.py",
                    "src/telemetry/service.py",
                ],
            )
        ],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    minted = [c for c in patched.components if c.name == "Telemetry"]
    assert len(minted) == 1
    assert minted[0].component_id == "1.4"
    file_paths = sorted(group.file_path for group in minted[0].file_methods)
    assert file_paths == [
        "src/telemetry/event.py",
        "src/telemetry/queue.py",
        "src/telemetry/service.py",
    ]
    # Methods came from the analysis files index, not the LLM payload.
    queue_group = next(g for g in minted[0].file_methods if g.file_path == "src/telemetry/queue.py")
    assert [m.qualified_name for m in queue_group.methods] == ["telemetry.queue.enqueue"]


def test_apply_scope_patch_owned_files_outside_unallocated_set_are_ignored() -> None:
    """The LLM cannot relocate files that aren't in `unallocated_files` —
    that would silently strip them from their existing owner."""
    analysis = _make_analysis_with_unallocated_files()
    patch_scope = PatchScope(
        scope_id="1",
        target_component_ids=["1.1", "1.2", "1.3"],
        visited_methods=[],
        impacted_methods=[],
        unallocated_files=["src/telemetry/event.py"],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[],
        new_components=[
            NewComponentSpec(
                name="Mixed",
                description="Tries to claim a file that isn't unallocated.",
                key_entities=[],
                owned_files=["src/telemetry/event.py", "src/auth.py"],
            )
        ],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    minted = next(c for c in patched.components if c.name == "Mixed")
    file_paths = sorted(group.file_path for group in minted.file_methods)
    assert file_paths == ["src/telemetry/event.py"]
    # Auth still owns src/auth.py (no silent reassignment).
    auth = next(c for c in patched.components if c.component_id == "1.1")
    assert [g.file_path for g in auth.file_methods] == ["src/auth.py"]


def test_apply_scope_patch_added_files_extend_existing_component_file_methods() -> None:
    analysis = _make_analysis_with_unallocated_files()
    patch_scope = PatchScope(
        scope_id="1",
        target_component_ids=["1.1", "1.2", "1.3"],
        visited_methods=[],
        impacted_methods=[],
        unallocated_files=[
            "src/telemetry/event.py",
            "src/telemetry/queue.py",
        ],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[
            ComponentPatch(
                component_id="1.2",
                description="Store + telemetry buffering.",
                key_entities=[SourceCodeReference(qualified_name="store.save")],
                added_files=["src/telemetry/event.py", "src/telemetry/queue.py"],
            )
        ],
        new_components=[],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    store = next(c for c in patched.components if c.component_id == "1.2")
    file_paths = sorted(group.file_path for group in store.file_methods)
    assert file_paths == ["src/store.py", "src/telemetry/event.py", "src/telemetry/queue.py"]
    queue_group = next(g for g in store.file_methods if g.file_path == "src/telemetry/queue.py")
    assert [m.qualified_name for m in queue_group.methods] == ["telemetry.queue.enqueue"]


def test_apply_scope_patch_double_attribution_resolves_to_first_consumer() -> None:
    """If the LLM lists the same unallocated file under two consumers,
    the first one wins. This guards against the LLM duplicating a file
    into both an existing component (`added_files`) and a new one
    (`owned_files`)."""
    analysis = _make_analysis_with_unallocated_files()
    patch_scope = PatchScope(
        scope_id="1",
        target_component_ids=["1.1", "1.2", "1.3"],
        visited_methods=[],
        impacted_methods=[],
        unallocated_files=["src/telemetry/event.py"],
    )
    scope_patch = AnalysisScopePatch(
        scope_description=None,
        components=[
            ComponentPatch(
                component_id="1.2",
                description="Store with the new event file.",
                key_entities=[SourceCodeReference(qualified_name="store.save")],
                added_files=["src/telemetry/event.py"],
            )
        ],
        new_components=[
            NewComponentSpec(
                name="Telemetry",
                description="Telemetry subsystem.",
                key_entities=[],
                owned_files=["src/telemetry/event.py"],
            )
        ],
        relations=[],
    )

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    store = next(c for c in patched.components if c.component_id == "1.2")
    telemetry = next(c for c in patched.components if c.name == "Telemetry")
    # The existing-component branch runs first → Store owns the file.
    assert "src/telemetry/event.py" in [g.file_path for g in store.file_methods]
    assert [g.file_path for g in telemetry.file_methods] == []
