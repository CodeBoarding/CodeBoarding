"""Inline a clustering payload into the standalone viewer page."""

import json
from pathlib import Path

_TEMPLATE = Path(__file__).parent / "viewer.html"
_PLACEHOLDER = "/*__CLUSTERING_PAYLOAD__*/null"


def render_html(payload: dict) -> str:
    """Return the viewer as one self-contained HTML document with the data embedded."""
    template = _TEMPLATE.read_text(encoding="utf-8")
    if _PLACEHOLDER not in template:
        raise ValueError(f"viewer template lost its payload placeholder: {_TEMPLATE}")
    # </script> inside the data would close the tag early; < keeps it inert and valid JSON.
    data = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    return template.replace(_PLACEHOLDER, data)
