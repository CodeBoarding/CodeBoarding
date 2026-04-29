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
from diagram_analysis.incremental_models import TraceResult, TraceStopReason
from diagram_analysis.incremental_updater import FileDelta, IncrementalDelta, MethodChange, apply_method_delta
from diagram_analysis.analysis_patcher import PatchScope
from diagram_analysis.scope_planner import apply_patch_scopes, build_ownership_index, derive_patch_scopes


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

    with patch("diagram_analysis.scope_planner.patch_analysis_scope", return_value=None):
        with pytest.raises(RuntimeError, match="Patch generation failed"):
            apply_patch_scopes(root, {}, patch_scopes, agent_llm=MagicMock())
