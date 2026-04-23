"""Round-trip tests for EASE encoding/decoding."""

from diagram_analysis.ease import ease_decode, ease_encode


def test_encode_decode_roundtrip_preserves_order():
    original = {
        "description": "root",
        "components": [
            {"id": "A", "name": "alpha"},
            {"id": "B", "name": "beta"},
            {"id": "C", "name": "gamma"},
        ],
    }
    encoded = ease_encode(original, ["components"])
    assert isinstance(encoded["components"], dict)
    assert encoded["components"]["display_order"] == ["aa", "ab", "ac"]
    assert encoded["components"]["aa"] == original["components"][0]

    decoded = ease_decode(encoded, ["components"])
    assert decoded == original


def test_encode_skips_non_listed_fields():
    original = {"a": [1, 2], "b": [3, 4]}
    encoded = ease_encode(original, ["a"])
    assert isinstance(encoded["a"], dict)
    assert encoded["b"] == [3, 4]


def test_decode_respects_display_order_even_when_reordered():
    original = {"items": [{"v": 1}, {"v": 2}, {"v": 3}]}
    encoded = ease_encode(original, ["items"])
    encoded["items"]["display_order"] = ["ac", "aa", "ab"]
    decoded = ease_decode(encoded, ["items"])
    assert [i["v"] for i in decoded["items"]] == [3, 1, 2]


def test_decode_tolerates_empty_arrays():
    original: dict = {"items": []}
    encoded = ease_encode(original, ["items"])
    assert encoded["items"] == {"display_order": []}
    decoded = ease_decode(encoded, ["items"])
    assert decoded == original
