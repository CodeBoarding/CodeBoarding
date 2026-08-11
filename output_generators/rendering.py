"""Render documentation from ``analysis.json``."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents.agent_responses import AnalysisInsights, Relation
from agents.relation_edges import append_or_merge_relation
from diagram_analysis.analysis_json import build_id_to_name_map, parse_unified_analysis
from output_generators.html import generate_html_file
from output_generators.markdown import generate_markdown_file
from output_generators.mdx import generate_mdx_file
from output_generators.sphinx import generate_rst_file
from utils import sanitize

logger = logging.getLogger(__name__)


def _ancestor_in_level(component_id: str, level_ids: set[str]) -> str | None:
    """Return the closest ancestor present in level_ids."""
    parts = component_id.split(".")
    for index in range(len(parts), 0, -1):
        ancestor = ".".join(parts[:index])
        if ancestor in level_ids:
            return ancestor
    return None


def project_relations_to_level(
    global_relations: list[Relation],
    level_component_ids: set[str],
    id_to_name: dict[str, str],
) -> list[Relation]:
    """Roll up global leaf relations onto the components visible at a level."""
    aggregated: list[Relation] = []
    for rel in global_relations:
        src = _ancestor_in_level(rel.src_id, level_component_ids)
        dst = _ancestor_in_level(rel.dst_id, level_component_ids)
        if src is None or dst is None or src == dst:
            continue
        append_or_merge_relation(
            aggregated,
            Relation(
                relation=rel.relation,
                src_name=id_to_name.get(src, src),
                dst_name=id_to_name.get(dst, dst),
                evidence=rel.evidence,
                key_edges=rel.key_edges,
                src_id=src,
                dst_id=dst,
                is_static=rel.is_static,
                all_edges=rel.all_edges,
            ),
            key=(src, dst),
        )
    return aggregated


# Writer-name lookup (resolved at call time so @patch on this module's names works).
# Only ``.md`` accepts ``demo``.
_FORMAT_WRITERS: dict[str, tuple[str, bool]] = {
    ".md": ("generate_markdown_file", True),
    ".html": ("generate_html_file", False),
    ".mdx": ("generate_mdx_file", False),
    ".rst": ("generate_rst_file", False),
}

FORMAT_EXTENSIONS = {
    "md": ".md",
    "html": ".html",
    "mdx": ".mdx",
    "rst": ".rst",
}
SUPPORTED_FORMATS = tuple(FORMAT_EXTENSIONS)


def _load_entries(analysis_path: Path) -> list[tuple[str, AnalysisInsights, set[str]]]:
    """Load the root and sub-analyses with relations projected to each level."""
    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    root_analysis, sub_analyses = parse_unified_analysis(data)
    id_to_name = build_id_to_name_map(root_analysis, sub_analyses)
    global_relations = list(root_analysis.components_relations)

    root_ids = {c.component_id for c in root_analysis.components}
    root_analysis.components_relations = project_relations_to_level(global_relations, root_ids, id_to_name)
    root_expanded = set(sub_analyses.keys())
    entries: list[tuple[str, AnalysisInsights, set[str]]] = [("__root__", root_analysis, root_expanded)]

    for comp_id, sub_analysis in sub_analyses.items():
        sub_ids = {c.component_id for c in sub_analysis.components}
        sub_analysis.components_relations = project_relations_to_level(global_relations, sub_ids, id_to_name)
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
    """Render docs in one extension under ``temp_dir``."""
    if format not in _FORMAT_WRITERS:
        raise ValueError(f"Unsupported extension: {format}")

    writer_name, accepts_demo = _FORMAT_WRITERS[format]
    writer: Callable[..., Any] = globals()[writer_name]
    named_entries = [
        (root_name if fname == "__root__" else fname, analysis, expanded)
        for fname, analysis, expanded in _load_entries(analysis_path)
    ]
    names_by_key: dict[str, str] = {}
    for out_name, _, _ in named_entries:
        key = out_name.casefold()
        if key in names_by_key:
            existing = names_by_key[key]
            raise ValueError(f"Output filename collision: {existing}{format} and {out_name}{format}")
        names_by_key[key] = out_name

    for out_name, analysis, expanded in named_entries:
        logger.info("Generating %s for: %s", format, out_name)
        kwargs: dict[str, Any] = {
            "repo_ref": repo_ref,
            "expanded_components": expanded,
            "temp_dir": temp_dir,
        }
        if accepts_demo:
            kwargs["demo"] = demo_mode
        writer(out_name, analysis, repo_name, **kwargs)


def render(
    output_format: str | None,
    *,
    analysis_path: Path,
    repo_name: str,
    output_dir: Path,
    repo_ref: str = "",
    root_name: str = "overview",
    demo_mode: bool = False,
) -> None:
    """Replace rendered docs for one format from a completed analysis.json."""
    if output_format is None:
        return
    try:
        extension = FORMAT_EXTENSIONS[output_format]
    except KeyError as exc:
        raise ValueError(f"Unsupported output format: {output_format}") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".render-", dir=output_dir) as staging_dir:
        staging_path = Path(staging_dir)
        render_docs(
            analysis_path=analysis_path,
            repo_name=repo_name,
            repo_ref=repo_ref,
            temp_dir=staging_path,
            format=extension,
            root_name=root_name,
            demo_mode=demo_mode,
        )
        generated_files = list(staging_path.glob(f"*{extension}"))
        for existing_file in output_dir.glob(f"*{extension}"):
            existing_file.unlink()
        for generated_file in generated_files:
            generated_file.replace(output_dir / generated_file.name)
