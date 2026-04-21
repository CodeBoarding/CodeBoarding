"""Round-trip tests for the analysis patcher (no LLM required)."""

import jsonpatch
import jsonpointer

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    Relation,
    SourceCodeReference,
    assign_component_ids,
)
from diagram_analysis.analysis_patcher import (
    _apply_patches,
    _decode_sub_analysis,
    _encode_sub_analysis,
    _sub_analysis_to_dict,
    _validate_patched,
)


def _make_sub_analysis() -> AnalysisInsights:
    files = {
        "src/core.py": FileEntry(
            methods=[
                MethodEntry(qualified_name="src.core.run", start_line=1, end_line=10, node_type="FUNCTION"),
            ]
        ),
    }
    component = Component(
        name="Core",
        description="Core runtime orchestrator.",
        key_entities=[SourceCodeReference(qualified_name="src.core.run", reference_file="src/core.py")],
        file_methods=[
            FileMethodGroup(
                file_path="src/core.py",
                methods=[
                    MethodEntry(qualified_name="src.core.run", start_line=1, end_line=10, node_type="FUNCTION"),
                ],
            )
        ],
    )
    analysis = AnalysisInsights(
        description="sub",
        components=[component],
        components_relations=[
            Relation(relation="uses", src_name="Core", dst_name="Core"),
        ],
        files=files,
    )
    assign_component_ids(analysis)
    return analysis


def test_sub_analysis_roundtrip_no_patch():
    sub = _make_sub_analysis()
    encoded = _encode_sub_analysis(_sub_analysis_to_dict(sub))
    decoded = _decode_sub_analysis(encoded)
    validated = _validate_patched(decoded, sub)
    assert validated is not None
    assert validated.description == sub.description
    assert len(validated.components) == 1
    assert validated.components[0].name == "Core"


def test_replace_description_via_patch_roundtrip():
    sub = _make_sub_analysis()
    encoded = _encode_sub_analysis(_sub_analysis_to_dict(sub))

    component_key = encoded["components"]["display_order"][0]
    patch_ops = [
        {
            "op": "replace",
            "path": f"/components/{component_key}/description",
            "value": "Core runtime orchestrator with updated semantics.",
        }
    ]

    patched = _apply_patches(encoded, patch_ops)
    decoded = _decode_sub_analysis(patched)
    validated = _validate_patched(decoded, sub)
    assert validated is not None
    assert validated.components[0].description == "Core runtime orchestrator with updated semantics."


def test_patch_preserves_files_field():
    sub = _make_sub_analysis()
    encoded = _encode_sub_analysis(_sub_analysis_to_dict(sub))
    component_key = encoded["components"]["display_order"][0]
    patch_ops = [
        {
            "op": "replace",
            "path": f"/components/{component_key}/name",
            "value": "Renamed",
        }
    ]
    patched = _apply_patches(encoded, patch_ops)
    decoded = _decode_sub_analysis(patched)
    # Files should not be in the encoded/decoded dict (excluded from patching surface)
    assert "files" not in decoded
    validated = _validate_patched(decoded, sub)
    assert validated is not None
    # Files field re-grafted from the original
    assert "src/core.py" in validated.files


def test_invalid_patch_path_raises_json_pointer_exception():
    sub = _make_sub_analysis()
    encoded = _encode_sub_analysis(_sub_analysis_to_dict(sub))
    patch_ops = [{"op": "replace", "path": "/nonexistent/field", "value": "x"}]
    try:
        _apply_patches(encoded, patch_ops)
    except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException):
        pass
    else:
        raise AssertionError("Expected JsonPatch or JsonPointer exception for invalid path")
