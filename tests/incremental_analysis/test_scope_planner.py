from unittest.mock import MagicMock, patch

import pytest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    SourceCodeReference,
)
from agents.change_status import ChangeStatus
from incremental_analysis.models import TraceResult, TraceStopReason
from incremental_analysis.delta_application import apply_method_delta
from incremental_analysis.updater import FileDelta, IncrementalDelta, MethodChange
from incremental_analysis.analysis_patcher import PatchScope
from incremental_analysis.scope_planner import apply_patch_scopes, build_ownership_index, derive_patch_scopes


def _method(name: str, start: int = 1, end: int = 1) -> MethodEntry:
    return MethodEntry(qualified_name=name, start_line=start, end_line=end, node_type="FUNCTION")


def _component(
    component_id: str, name: str, file_path: str | None = None, methods: list[MethodEntry] | None = None
) -> Component:
    return Component(
        name=name,
        description=f"{name} description",
        key_entities=[SourceCodeReference(qualified_name=methods[0].qualified_name)] if methods else [],
        component_id=component_id,
        file_methods=[] if file_path is None else [FileMethodGroup(file_path=file_path, methods=methods or [])],
    )


def test_derive_patch_scopes_short_circuits_cosmetic_only_with_no_gaps():
    """Cosmetic-only stops with no impacted methods and no coverage gaps
    must not schedule a patcher LLM call. Without this gate, the seeded
    ``visited_methods`` from the trace would be routed into target
    components, producing a no-op patch run that costs ~10-20s of LLM
    time per refresh."""
    method = _method("mod.foo", 1, 2)
    root = AnalysisInsights(
        description="root",
        files={"src/mod.py": FileEntry(methods=[method])},
        components=[_component("1.1", "Auth", "src/mod.py", [method])],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {})
    trace_result = TraceResult(
        visited_methods=["mod.foo"],
        impacted_methods=[],
        stop_reason=TraceStopReason.COSMETIC_ONLY,
    )

    patch_scopes = derive_patch_scopes(trace_result, root, {}, ownership_index)
    assert patch_scopes == []


def test_derive_patch_scopes_short_circuits_no_material_impact_with_no_gaps():
    """Same gate as the cosmetic case, but for the LLM-judged "no
    material impact" terminal stop."""
    method = _method("mod.foo", 1, 2)
    root = AnalysisInsights(
        description="root",
        files={"src/mod.py": FileEntry(methods=[method])},
        components=[_component("1.1", "Auth", "src/mod.py", [method])],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {})
    trace_result = TraceResult(
        visited_methods=["mod.foo"],
        impacted_methods=[],
        stop_reason=TraceStopReason.NO_MATERIAL_IMPACT,
    )

    assert derive_patch_scopes(trace_result, root, {}, ownership_index) == []


def test_derive_patch_scopes_does_not_short_circuit_cosmetic_with_impacted_methods():
    """``impacted_methods`` populated by the trace means there *is*
    something to patch even if the stop reason was cosmetic. Don't
    drop those."""
    method = _method("mod.foo", 1, 2)
    root = AnalysisInsights(
        description="root",
        files={"src/mod.py": FileEntry(methods=[method])},
        components=[_component("1.1", "Auth", "src/mod.py", [method])],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {})
    trace_result = TraceResult(
        visited_methods=["mod.foo"],
        impacted_methods=["mod.foo"],
        stop_reason=TraceStopReason.COSMETIC_ONLY,
    )

    patch_scopes = derive_patch_scopes(trace_result, root, {}, ownership_index)
    assert len(patch_scopes) == 1


def test_derive_patch_scopes_does_not_short_circuit_when_files_disconnected():
    """Coverage-gap stops (non_traceable_files / disconnected_files)
    legitimately need patching even when impacted_methods is empty."""
    method = _method("mod.foo", 1, 2)
    root = AnalysisInsights(
        description="root",
        files={"src/mod.py": FileEntry(methods=[method])},
        components=[_component("1.1", "Auth", "src/mod.py", [method])],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {})
    trace_result = TraceResult(
        visited_methods=["mod.foo"],
        impacted_methods=[],
        non_traceable_files=["src/mod.py"],
        stop_reason=TraceStopReason.NO_MATERIAL_IMPACT,
    )

    patch_scopes = derive_patch_scopes(trace_result, root, {}, ownership_index)
    assert len(patch_scopes) == 1


def test_derive_patch_scopes_maps_new_methods_after_delta_application():
    existing = _method("mod.existing", 1, 2)
    root = AnalysisInsights(
        description="root",
        files={"src/mod.py": FileEntry(methods=[existing])},
        components=[_component("1.1", "Auth", "src/mod.py", [existing])],
        components_relations=[],
    )
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/mod.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1.1",
                added_methods=[
                    MethodChange(
                        qualified_name="mod.added",
                        file_path="src/mod.py",
                        start_line=4,
                        end_line=5,
                        change_type=ChangeStatus.ADDED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )

    updated_root = root.model_copy(deep=True)
    updated_subs: dict[str, AnalysisInsights] = {}
    apply_method_delta(updated_root, updated_subs, delta)
    ownership_index = build_ownership_index(updated_root, updated_subs)
    trace_result = TraceResult(
        visited_methods=["mod.added"],
        impacted_methods=["mod.added"],
        stop_reason=TraceStopReason.CLOSURE_REACHED,
    )

    patch_scopes = derive_patch_scopes(trace_result, updated_root, updated_subs, ownership_index)

    assert len(patch_scopes) == 1
    assert patch_scopes[0].scope_id is None
    assert patch_scopes[0].target_component_ids == ["1.1"]
    assert patch_scopes[0].visited_methods == ["mod.added"]


def test_derive_patch_scopes_preserves_semantic_impact_summary():
    method = _method("mod.foo", 1, 2)
    root = AnalysisInsights(
        description="root",
        files={"src/mod.py": FileEntry(methods=[method])},
        components=[_component("1.1", "Auth", "src/mod.py", [method])],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {})
    trace_result = TraceResult(
        visited_methods=["mod.foo"],
        impacted_methods=["mod.foo"],
        stop_reason=TraceStopReason.CLOSURE_REACHED,
        semantic_impact_summary="The request flow changed. Persistence behavior changed.",
    )

    patch_scopes = derive_patch_scopes(trace_result, root, {}, ownership_index)

    assert len(patch_scopes) == 1
    assert patch_scopes[0].semantic_impact_summary == ("The request flow changed. Persistence behavior changed.")


def test_derive_patch_scopes_widens_to_descendants_in_scope():
    child_a = _method("pkg.a.run", 1, 2)
    child_b = _method("pkg.b.run", 1, 2)
    root = AnalysisInsights(
        description="root",
        files={
            "pkg/a.py": FileEntry(methods=[child_a]),
            "pkg/b.py": FileEntry(methods=[child_b]),
        },
        components=[_component("1.2", "Parent")],
        components_relations=[],
    )
    sub_analysis = AnalysisInsights(
        description="sub",
        files=root.files,
        components=[
            _component("1.2.1", "ChildA", "pkg/a.py", [child_a]),
            _component("1.2.2", "ChildB", "pkg/b.py", [child_b]),
        ],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {"1.2": sub_analysis})
    trace_result = TraceResult(
        visited_methods=["pkg.a.run", "pkg.b.run"],
        impacted_methods=["pkg.a.run"],
        stop_reason=TraceStopReason.UNCERTAIN,
    )

    patch_scopes = derive_patch_scopes(trace_result, root, {"1.2": sub_analysis}, ownership_index)

    assert len(patch_scopes) == 1
    assert patch_scopes[0].scope_id == "1.2"
    assert patch_scopes[0].target_component_ids == ["1.2.1", "1.2.2"]


def test_apply_patch_scopes_raises_when_patch_generation_fails():
    root = AnalysisInsights(
        description="root",
        files={},
        components=[_component("1.1", "Auth")],
        components_relations=[],
    )
    patch_scopes = [
        PatchScope(
            scope_id=None,
            target_component_ids=["1.1"],
            visited_methods=["auth.login"],
            impacted_methods=["auth.login"],
        )
    ]

    with patch("incremental_analysis.scope_planner.patch_analysis_scope", return_value=None):
        with pytest.raises(RuntimeError, match="Patch generation failed"):
            apply_patch_scopes(root, {}, patch_scopes, agent_llm=MagicMock())


def test_derive_patch_scopes_routes_unallocated_files_to_closest_components_subscope():
    """An unallocated file is routed to the sub-scope of its closest existing
    component (when that component has a sub-analysis), so a freshly-minted
    component lands as a *child*, not a top-level peer."""
    auth_method = _method("backend.auth.login", 1, 5)
    store_method = _method("backend.store.save", 1, 5)
    root = AnalysisInsights(
        description="root",
        files={
            "backend/auth.py": FileEntry(methods=[auth_method]),
            "backend/store.py": FileEntry(methods=[store_method]),
            "backend/telemetry/service.py": FileEntry(methods=[_method("backend.telemetry.flush", 1, 5)]),
        },
        components=[_component("1", "Backend", "backend/auth.py", [auth_method])],
        components_relations=[],
    )
    sub_analyses = {
        "1": AnalysisInsights(
            description="sub of 1",
            files=root.files,
            components=[
                _component("1.1", "Auth", "backend/auth.py", [auth_method]),
                _component("1.2", "Store", "backend/store.py", [store_method]),
            ],
            components_relations=[],
        ),
    }
    ownership_index = build_ownership_index(root, sub_analyses)
    trace_result = TraceResult(
        visited_methods=[],
        impacted_methods=[],
        stop_reason=TraceStopReason.NO_MATERIAL_IMPACT,
    )

    patch_scopes = derive_patch_scopes(
        trace_result,
        root,
        sub_analyses,
        ownership_index,
        unallocated_files=["backend/telemetry/service.py"],
    )

    # backend/telemetry/service.py's closest-by-prefix component is one of
    # backend/* owners (1.1 or 1.2); since `1` has a sub-analysis, the file
    # is routed to scope_id="1" so the new component becomes a child of [1].
    matching = [ps for ps in patch_scopes if ps.scope_id == "1"]
    assert len(matching) == 1
    assert matching[0].unallocated_files == ["backend/telemetry/service.py"]
    # Targets default to all components in the scope so the LLM can pick any
    # for fold-in or mint a new one.
    assert set(matching[0].target_component_ids) >= {"1.1", "1.2"}


def test_derive_patch_scopes_routes_to_root_when_closest_component_is_unexpanded():
    """When the closest existing component has no sub-analysis (it's a leaf),
    the unallocated file routes to where that component lives, so a mint
    becomes a sibling — second-best when no expansion exists yet."""
    method = _method("svc.run", 1, 5)
    root = AnalysisInsights(
        description="root",
        files={
            "src/svc.py": FileEntry(methods=[method]),
            "src/feature/new.py": FileEntry(methods=[_method("svc.feature.run", 1, 5)]),
        },
        components=[_component("1", "Svc", "src/svc.py", [method])],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {})
    trace_result = TraceResult(
        visited_methods=[],
        impacted_methods=[],
        stop_reason=TraceStopReason.NO_MATERIAL_IMPACT,
    )

    patch_scopes = derive_patch_scopes(
        trace_result,
        root,
        {},
        ownership_index,
        unallocated_files=["src/feature/new.py"],
    )

    # No sub-analysis for "1" → fall back to the scope where "1" lives (None).
    matching = [ps for ps in patch_scopes if ps.scope_id is None]
    assert len(matching) == 1
    assert matching[0].unallocated_files == ["src/feature/new.py"]
    assert "1" in matching[0].target_component_ids


def test_derive_patch_scopes_unallocated_files_skip_no_change_short_circuit():
    """Even with a cosmetic-only stop and no other gaps, unallocated files
    must still produce a patch scope so the LLM can place them."""
    root = AnalysisInsights(
        description="root",
        files={"src/new.py": FileEntry(methods=[_method("new.fn", 1, 2)])},
        components=[],
        components_relations=[],
    )
    ownership_index = build_ownership_index(root, {})
    trace_result = TraceResult(
        visited_methods=[],
        impacted_methods=[],
        stop_reason=TraceStopReason.COSMETIC_ONLY,
    )

    patch_scopes = derive_patch_scopes(
        trace_result,
        root,
        {},
        ownership_index,
        unallocated_files=["src/new.py"],
    )

    assert len(patch_scopes) == 1
    assert patch_scopes[0].unallocated_files == ["src/new.py"]
