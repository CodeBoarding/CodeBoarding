"""Tests for the EASE-encoded analysis patcher."""

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from diagram_analysis.analysis_patcher import (
    _apply_patches,
    _decode_sub_analysis,
    _encode_sub_analysis,
    _sub_analysis_to_dict,
    _validate_patched,
    merge_patched_sub_analyses,
)
from diagram_analysis.ease import ease_encode


def _make_sub_analysis() -> AnalysisInsights:
    return AnalysisInsights(
        description="Auth subsystem",
        components=[
            Component(
                name="Login",
                component_id="1.1",
                description="Handles user login",
                key_entities=[
                    SourceCodeReference(qualified_name="auth.login"),
                ],
            ),
            Component(
                name="Logout",
                component_id="1.2",
                description="Handles user logout",
                key_entities=[
                    SourceCodeReference(qualified_name="auth.logout"),
                ],
            ),
        ],
        components_relations=[
            Relation(relation="uses", src_name="Login", dst_name="Logout"),
        ],
    )


def test_sub_analysis_to_dict():
    sub = _make_sub_analysis()
    d = _sub_analysis_to_dict(sub)

    assert d["description"] == "Auth subsystem"
    assert len(d["components"]) == 2
    assert d["components"][0]["name"] == "Login"
    assert d["components"][0]["key_entities"][0]["qualified_name"] == "auth.login"
    assert len(d["components_relations"]) == 1


def test_encode_decode_roundtrip():
    sub = _make_sub_analysis()
    d = _sub_analysis_to_dict(sub)
    encoded = _encode_sub_analysis(d)

    # Verify EASE encoding
    assert isinstance(encoded["components"], dict)
    assert "display_order" in encoded["components"]
    assert isinstance(encoded["components_relations"], dict)

    # Verify nested encoding
    first_comp_key = encoded["components"]["display_order"][0]
    comp = encoded["components"][first_comp_key]
    assert isinstance(comp["key_entities"], dict)
    assert "display_order" in comp["key_entities"]

    # Decode and verify roundtrip
    decoded = _decode_sub_analysis(encoded)
    assert decoded["description"] == d["description"]
    assert len(decoded["components"]) == 2
    assert decoded["components"][0]["name"] == "Login"
    assert len(decoded["components_relations"]) == 1


def test_apply_patches_replace():
    encoded = ease_encode({"items": [{"name": "old"}]}, ["items"])
    patch_ops = [{"op": "replace", "path": "/items/aa/name", "value": "new"}]
    result = _apply_patches(encoded, patch_ops)
    assert result["items"]["aa"]["name"] == "new"


def test_apply_patches_add():
    encoded = ease_encode({"items": [{"name": "first"}]}, ["items"])
    patch_ops = [
        {"op": "add", "path": "/items/ab", "value": {"name": "second"}},
        {"op": "add", "path": "/items/display_order/-", "value": "ab"},
    ]
    result = _apply_patches(encoded, patch_ops)
    assert "ab" in result["items"]
    assert result["items"]["ab"]["name"] == "second"
    assert "ab" in result["items"]["display_order"]


def test_apply_patches_remove():
    encoded = ease_encode({"items": [{"name": "first"}, {"name": "second"}]}, ["items"])
    # Remove second item
    patch_ops = [
        {"op": "remove", "path": "/items/ab"},
    ]
    result = _apply_patches(encoded, patch_ops)
    assert "ab" not in result["items"]


def test_validate_patched_valid():
    decoded = {
        "description": "Test",
        "components": [
            {"name": "A", "description": "Comp A", "key_entities": []},
        ],
        "components_relations": [
            {"relation": "uses", "src_name": "A", "dst_name": "B"},
        ],
    }
    result = _validate_patched(decoded)
    assert result is not None
    assert isinstance(result, AnalysisInsights)
    assert result.description == "Test"
    assert len(result.components) == 1
    assert len(result.components_relations) == 1


def test_validate_patched_invalid():
    # Missing required fields
    decoded = {"components": [{"no_name_field": True}]}
    result = _validate_patched(decoded)
    assert result is None


def test_merge_patched_sub_analyses():
    original = {
        "1": _make_sub_analysis(),
        "2": AnalysisInsights(description="Other", components=[], components_relations=[]),
    }
    patched_sub = AnalysisInsights(
        description="Auth subsystem (updated)",
        components=[
            Component(name="Login", component_id="1.1", description="Updated login", key_entities=[]),
        ],
        components_relations=[],
    )

    merge_patched_sub_analyses(original, {"1": patched_sub})
    assert original["1"].description == "Auth subsystem (updated)"
    assert original["2"].description == "Other"  # Untouched


def test_merge_patched_unknown_id():
    original = {"1": _make_sub_analysis()}
    patched_sub = AnalysisInsights(description="X", components=[], components_relations=[])
    # Should log a warning but not crash
    merge_patched_sub_analyses(original, {"999": patched_sub})
    assert "1" in original
    assert "999" not in original
