"""Tests for EASE (Element-Addressed Stable Encoding) encode/decode."""

from diagram_analysis.ease import ease_decode, ease_encode


def test_encode_single_array():
    obj = {"name": "test", "items": [{"a": 1}, {"b": 2}, {"c": 3}]}
    result = ease_encode(obj, ["items"])

    assert result["name"] == "test"
    assert isinstance(result["items"], dict)
    assert "display_order" in result["items"]
    assert len(result["items"]["display_order"]) == 3
    assert result["items"]["aa"] == {"a": 1}
    assert result["items"]["ab"] == {"b": 2}
    assert result["items"]["ac"] == {"c": 3}
    assert result["items"]["display_order"] == ["aa", "ab", "ac"]


def test_encode_empty_array():
    obj = {"items": []}
    result = ease_encode(obj, ["items"])
    assert result["items"] == {"display_order": []}


def test_encode_non_listed_field_unchanged():
    obj = {"items": [1, 2], "other": "keep"}
    result = ease_encode(obj, ["items"])
    assert result["other"] == "keep"


def test_encode_field_not_an_array_unchanged():
    obj = {"items": "not-a-list"}
    result = ease_encode(obj, ["items"])
    assert result["items"] == "not-a-list"


def test_decode_roundtrip():
    original = {
        "description": "test",
        "components": [
            {"name": "A", "description": "Component A"},
            {"name": "B", "description": "Component B"},
        ],
        "relations": [
            {"src": "A", "dst": "B", "relation": "calls"},
        ],
    }
    encoded = ease_encode(original, ["components", "relations"])
    decoded = ease_decode(encoded, ["components", "relations"])

    assert decoded["description"] == original["description"]
    assert decoded["components"] == original["components"]
    assert decoded["relations"] == original["relations"]


def test_decode_preserves_display_order():
    encoded = {
        "items": {
            "ac": {"val": 3},
            "aa": {"val": 1},
            "ab": {"val": 2},
            "display_order": ["aa", "ab", "ac"],
        }
    }
    result = ease_decode(encoded, ["items"])
    assert result["items"] == [{"val": 1}, {"val": 2}, {"val": 3}]


def test_decode_extra_keys_appended():
    """Keys not in display_order are appended after ordered ones."""
    encoded = {
        "items": {
            "aa": "first",
            "zz": "extra",
            "display_order": ["aa"],
        }
    }
    result = ease_decode(encoded, ["items"])
    assert result["items"] == ["first", "extra"]


def test_decode_non_dict_unchanged():
    obj = {"items": "string-value"}
    result = ease_decode(obj, ["items"])
    assert result["items"] == "string-value"


def test_roundtrip_large_array():
    """Encode/decode 30 elements."""
    items = [{"id": i} for i in range(30)]
    encoded = ease_encode({"items": items}, ["items"])
    decoded = ease_decode(encoded, ["items"])
    assert decoded["items"] == items


def test_multiple_array_fields():
    obj = {
        "components": [{"name": "X"}],
        "relations": [{"rel": "uses"}],
        "scalar": 42,
    }
    encoded = ease_encode(obj, ["components", "relations"])
    assert isinstance(encoded["components"], dict)
    assert isinstance(encoded["relations"], dict)
    assert encoded["scalar"] == 42

    decoded = ease_decode(encoded, ["components", "relations"])
    assert decoded == obj
