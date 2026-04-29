"""Unified docs rendering from ``analysis.json``.

Collapses the four ``generate_markdown/html/mdx/rst`` functions in
``github_action.py`` and ``generate_markdown_docs`` in the former
``markdown.py`` into a single table-driven entry point.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents.agent_responses import AnalysisInsights
from diagram_analysis.analysis_json import build_id_to_name_map, parse_unified_analysis
from output_generators.html import generate_html_file
from output_generators.markdown import generate_markdown_file
from output_generators.mdx import generate_mdx_file
from output_generators.sphinx import generate_rst_file
from utils import sanitize

logger = logging.getLogger(__name__)

# Writer-name lookup (resolved at call time so @patch on this module's names works).
# Only ``.md`` accepts ``demo``.
_FORMAT_WRITERS: dict[str, tuple[str, bool]] = {
    ".md": ("generate_markdown_file", True),
    ".html": ("generate_html_file", False),
    ".mdx": ("generate_mdx_file", False),
    ".rst": ("generate_rst_file", False),
}


def _load_entries(analysis_path: Path) -> list[tuple[str, AnalysisInsights, set[str]]]:
    """Return ``(filename, analysis, expanded_component_ids)`` for root + each sub-analysis.

    The root entry's filename is supplied by the caller (see :func:`render_docs`);
    here we emit it as ``"__root__"`` for the caller to rename.
    """
    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    root_analysis, sub_analyses = parse_unified_analysis(data)
    id_to_name = build_id_to_name_map(root_analysis, sub_analyses)

    root_expanded = set(sub_analyses.keys())
    entries: list[tuple[str, AnalysisInsights, set[str]]] = [("__root__", root_analysis, root_expanded)]

    for comp_id, sub_analysis in sub_analyses.items():
        sub_expanded = {c.component_id for c in sub_analysis.components if c.component_id in sub_analyses}
        comp_name = id_to_name.get(comp_id, comp_id)
        entries.append((sanitize(comp_name), sub_analysis, sub_expanded))

    return entries


def render_docs(
    analysis_path: Path,
    *,
    repo_name: str,
    repo_ref: str,
    temp_dir: Path,
    format: str = ".md",
    root_name: str = "overview",
    demo_mode: bool = False,
) -> None:
    """Render an ``analysis.json`` into *format* docs under *temp_dir*.

    - ``repo_ref`` is the fully-formed link prefix (e.g.
      ``https://github.com/x/y/blob/main/.codeboarding``); this function
      does not construct it, because callers disagree on the tail segment.
    - ``root_name`` names the top-level file (``"overview"`` in the GitHub
      Action, ``"on_boarding"`` in the CLI workflow).
    - ``demo_mode`` is honored only by writers that accept it (currently
      markdown); it is silently ignored by others.
    """
    if format not in _FORMAT_WRITERS:
        raise ValueError(f"Unsupported extension: {format}")

    writer_name, accepts_demo = _FORMAT_WRITERS[format]
    writer: Callable[..., Any] = globals()[writer_name]
    for fname, analysis, expanded in _load_entries(analysis_path):
        out_name = root_name if fname == "__root__" else fname
        logger.info("Generating %s for: %s", format, out_name)
        kwargs: dict[str, Any] = {
            "repo_ref": repo_ref,
            "expanded_components": expanded,
            "temp_dir": temp_dir,
        }
        if accepts_demo:
            kwargs["demo"] = demo_mode
        writer(out_name, analysis, repo_name, **kwargs)
