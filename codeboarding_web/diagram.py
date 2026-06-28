"""Adapt a saved analysis.json into Cytoscape elements for the overview graph."""

import json
import logging
from pathlib import Path

from agents.agent_responses import AnalysisInsights, SourceCodeReference
from codeboarding_web.component_data import changed_files, component_files, load_warning_counts
from diagram_analysis.io_utils import parse_unified_analysis
from output_generators.html import generate_cytoscape_data
from utils import sanitize

logger = logging.getLogger(__name__)


def _open_url(repo_path: Path, ref: SourceCodeReference) -> str | None:
    """Return a vscode://file URI for *ref*, or None when location data is missing."""
    if not ref.reference_file or ref.reference_start_line is None:
        return None
    if Path(ref.reference_file).is_absolute():
        abs_path = ref.reference_file
    else:
        abs_path = str((repo_path / ref.reference_file).resolve())
    return f"vscode://file/{abs_path}:{ref.reference_start_line}"


def _entity(repo_path: Path, ref: SourceCodeReference) -> dict:
    """Serialize a SourceCodeReference to the keyEntities wire format."""
    return {
        "qname": ref.qualified_name,
        "file": ref.reference_file,
        "startLine": ref.reference_start_line,
        "endLine": ref.reference_end_line,
        "openUrl": _open_url(repo_path, ref),
    }


def _enrich(
    elements: dict,
    analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    repo_path: Path,
    warning_counts: dict[str, int],
    changed: set[str],
) -> None:
    """Mutate node data in *elements* to add componentId, expandable, keyEntities, warnings, fileWarnings, modifications, sourceFiles."""
    by_id = {sanitize(c.name): c for c in analysis.components}
    expandable_ids = set(sub_analyses.keys())
    for el in elements["elements"]:
        data = el["data"]
        if "source" in data:
            continue
        comp = by_id.get(data["id"])
        if comp is None:
            continue
        data["componentId"] = comp.component_id
        data["expandable"] = comp.component_id in expandable_ids
        data["keyEntities"] = [_entity(repo_path, ref) for ref in comp.key_entities]
        files = component_files(comp, repo_path)
        data["sourceFiles"] = sorted(files)
        data["warnings"] = sum(warning_counts.get(f, 0) for f in files)
        file_warnings: list[dict[str, int | str]] = [
            {"file": f, "warnings": warning_counts[f]} for f in files if warning_counts.get(f, 0) > 0
        ]
        data["fileWarnings"] = sorted(file_warnings, key=lambda fw: (-int(fw["warnings"]), fw["file"]))
        data["modifications"] = sum(1 for f in files if f in changed)


def load_cytoscape(output_dir: Path, project: str, repo_path: Path) -> dict | None:
    """Read ``analysis.json`` from *output_dir* and return enriched overview Cytoscape JSON.

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
    elements = generate_cytoscape_data(root_analysis, expanded, project, demo=False)
    warning_counts = load_warning_counts(output_dir)
    changed = changed_files(repo_path)
    _enrich(elements, root_analysis, sub_analyses, repo_path, warning_counts, changed)
    return elements


def load_cytoscape_component(output_dir: Path, project: str, repo_path: Path, component_id: str) -> dict | None:
    """Return enriched Cytoscape JSON for a single component's sub-graph, or None if absent."""
    path = output_dir / "analysis.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _, sub_analyses = parse_unified_analysis(data)
    except Exception:
        logger.debug("analysis.json not readable yet", exc_info=True)
        return None
    sub = sub_analyses.get(component_id)
    if sub is None:
        return None
    sub_expanded = {c.component_id for c in sub.components if c.component_id in sub_analyses}
    elements = generate_cytoscape_data(sub, sub_expanded, project, demo=False)
    warning_counts = load_warning_counts(output_dir)
    changed = changed_files(repo_path)
    _enrich(elements, sub, sub_analyses, repo_path, warning_counts, changed)
    return elements
