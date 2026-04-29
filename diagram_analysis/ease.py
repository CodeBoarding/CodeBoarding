"""EASE (Element-Addressed Stable Encoding) for LLM-friendly JSON patching.

Transforms JSON arrays into dicts with stable two-character keys and a
``display_order`` list, eliminating array-index arithmetic that LLMs get wrong.

Encode before sending to the LLM for patching; decode after applying patches.
"""

import string

# 676 two-char keys: "aa", "ab", ..., "az", "ba", ..., "zz"
_CHARS = string.ascii_lowercase
_KEY_POOL = [a + b for a in _CHARS for b in _CHARS]


def _next_key(used: set[str], pool: list[str], idx: int) -> tuple[str, int]:
    while idx < len(pool) and pool[idx] in used:
        idx += 1
    if idx >= len(pool):
        raise ValueError("EASE key pool exhausted (max 676 elements)")
    return pool[idx], idx + 1


def ease_encode(obj: dict, array_fields: list[str]) -> dict:
    """Encode specified array fields in *obj* into EASE dicts.

    Each array ``[elem0, elem1, ...]`` becomes::

        {"aa": elem0, "ab": elem1, ..., "display_order": ["aa", "ab", ...]}

    Non-listed fields are passed through unchanged.
    """
    result = {}
    for key, value in obj.items():
        if key in array_fields and isinstance(value, list):
            encoded: dict = {}
            order: list[str] = []
            pool_idx = 0
            used: set[str] = set()
            for item in value:
                ease_key, pool_idx = _next_key(used, _KEY_POOL, pool_idx)
                used.add(ease_key)
                encoded[ease_key] = item
                order.append(ease_key)
            encoded["display_order"] = order
            result[key] = encoded
        else:
            result[key] = value
    return result


def ease_decode(obj: dict, array_fields: list[str]) -> dict:
    """Decode EASE dicts back into ordered arrays.

    Reads ``display_order`` to reconstruct the original array ordering.
    Unknown keys not in ``display_order`` are appended at the end.
    """
    result = {}
    for key, value in obj.items():
        if key in array_fields and isinstance(value, dict):
            order = value.get("display_order", [])
            ordered_keys = list(order)
            extra = [k for k in value if k != "display_order" and k not in order]
            ordered_keys.extend(sorted(extra))
            result[key] = [value[k] for k in ordered_keys if k in value]
        else:
            result[key] = value
    return result
