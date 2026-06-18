"""Adapt a saved analysis.json into Cytoscape elements for the overview graph."""

import json
import logging
from pathlib import Path

from diagram_analysis.io_utils import parse_unified_analysis
from output_generators.html import generate_cytoscape_data

logger = logging.getLogger(__name__)


def load_cytoscape(output_dir: Path, project: str) -> dict | None:
    """Read ``analysis.json`` from *output_dir* and return overview Cytoscape JSON.

    Returns None when the file is missing or unreadable (e.g. mid-write).
    Why: the writer re-saves during a run; a transient parse failure must not
    crash the SSE stream.
    """
    path = output_dir / "analysis.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        root_analysis, sub_analyses = parse_unified_analysis(data)
    except Exception:
        logger.debug("analysis.json not readable yet", exc_info=True)
        return None
    expanded = set(sub_analyses.keys())
    return generate_cytoscape_data(root_analysis, expanded, project, demo=False)
