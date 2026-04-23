"""Generate a single LLM-oriented AGENTS.md-style digest of a CodeBoarding analysis.

Layout: YAML frontmatter, nested TOC with anchors, depth-tiered component blocks
(inline relations + hybrid id/name edge refs), and a trailing flat edge index.
No Mermaid — redundant with the indented text for LLM consumption.
"""

import re
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference


def _slug(text: str) -> str:
    """Render GFM-compatible heading anchor."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"\s+", "-", s).strip("-")


def _anchor(comp: Component) -> str:
    return _slug(f"{comp.component_id} {comp.name}")


def _one_line(text: str, limit: int = 100) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _ref_str(ref: SourceCodeReference, include_lines: bool = True) -> str:
    if not ref.reference_file:
        return f"`{ref.qualified_name}`"
    loc = ref.reference_file
    if include_lines and ref.reference_start_line and ref.reference_end_line:
        if ref.reference_start_line < ref.reference_end_line:
            loc += f":L{ref.reference_start_line}-L{ref.reference_end_line}"
    return f"`{ref.qualified_name}` — {loc}"


def _edge_bullet(rel: Relation) -> str:
    dst = f"`{rel.dst_id} {rel.dst_name}`" if rel.dst_id else f"`{rel.dst_name}`"
    return f"- {rel.relation} → {dst}"


def _component_block(
    comp: Component,
    outgoing: list[Relation],
    depth: int,
) -> list[str]:
    """Render a component. Depth tiers: 1=full, 2=terse, 3+=minimal.

    Why: GraphRAG-style tiering keeps the whole graph in one file without
    blowing the context budget. Leaf components only need names + edges; mid
    levels drop the exhaustive file_methods listing; the root tier carries
    full source detail.
    """
    heading_hashes = "#" * min(depth + 1, 6)
    lines = [f"{heading_hashes} {comp.component_id} {comp.name}", ""]

    desc = _one_line(comp.description, limit=140) if depth >= 3 else comp.description.strip()
    if desc:
        lines += [desc, ""]

    if outgoing:
        lines.append("**Relations:**")
        lines += [_edge_bullet(r) for r in outgoing]
        lines.append("")

    if comp.key_entities:
        lines.append("**Key entities:**")
        lines += [f"- {_ref_str(e, include_lines=depth < 3)}" for e in comp.key_entities]
        lines.append("")

    if depth == 1 and comp.file_methods:
        lines.append("**Source files:**")
        for fg in comp.file_methods:
            lines.append(f"- `{fg.file_path}`")
            for m in fg.methods:
                lines.append(f"  - `{m.qualified_name}` (L{m.start_line}-L{m.end_line}) — {m.node_type}")
        lines.append("")

    return lines


def _toc(root: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> list[str]:
    lines = ["## Components (TOC)", ""]

    def _walk(comp: Component, indent: int) -> None:
        pad = "  " * indent
        summary = _one_line(comp.description, limit=80)
        lines.append(f"{pad}- [{comp.component_id} {comp.name}](#{_anchor(comp)}) — {summary}")
        sub = sub_analyses.get(comp.component_id)
        if sub:
            for child in sub.components:
                _walk(child, indent + 1)

    for c in root.components:
        _walk(c, 0)
    lines.append("")
    return lines


def _render_tree(
    comp: Component,
    outgoing_by_name: dict[str, list[Relation]],
    sub_analyses: dict[str, AnalysisInsights],
    depth: int,
) -> list[str]:
    lines = _component_block(comp, outgoing_by_name.get(comp.name, []), depth)
    sub = sub_analyses.get(comp.component_id)
    if sub:
        child_outgoing = _group_by_src_name(sub.components_relations)
        for child in sub.components:
            lines += _render_tree(child, child_outgoing, sub_analyses, depth + 1)
    return lines


def _group_by_src_name(relations: list[Relation]) -> dict[str, list[Relation]]:
    """Key on src_name: always populated, while src_id may be blank on older analyses."""
    by_src: dict[str, list[Relation]] = {}
    for r in relations:
        by_src.setdefault(r.src_name, []).append(r)
    return by_src


def _edge_index(root: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> list[str]:
    """Flat edge listing. Cheap global-reasoning win (KG-LLM-Bench)."""
    lines = ["## Edge Index", ""]
    seen: set[tuple[str, str, str]] = set()

    def _emit(rel: Relation) -> None:
        key = (rel.src_id or rel.src_name, rel.relation, rel.dst_id or rel.dst_name)
        if key in seen:
            return
        seen.add(key)
        src = f"{rel.src_id} {rel.src_name}" if rel.src_id else rel.src_name
        dst = f"{rel.dst_id} {rel.dst_name}" if rel.dst_id else rel.dst_name
        lines.append(f"- `{src}` — {rel.relation} → `{dst}`")

    for rel in root.components_relations:
        _emit(rel)
    for sub in sub_analyses.values():
        for rel in sub.components_relations:
            _emit(rel)
    lines.append("")
    return lines


def generate_agents_md(
    root: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights] | None = None,
    project: str = "",
) -> str:
    """Render a flat AGENTS.md-style digest of root + sub-analyses."""
    sub_analyses = sub_analyses or {}

    lines: list[str] = [
        "---",
        f"project: {project}" if project else "project: unnamed",
        "kind: codeboarding-analysis",
        "---",
        "",
        f"# {project or 'Project'} — Architecture",
        "",
        root.description.strip(),
        "",
    ]
    lines += _toc(root, sub_analyses)

    outgoing = _group_by_src_name(root.components_relations)
    for comp in root.components:
        lines += _render_tree(comp, outgoing, sub_analyses, depth=1)

    lines += _edge_index(root, sub_analyses)
    return "\n".join(lines).rstrip() + "\n"


def generate_agents_md_file(
    root: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    project: str,
    output_dir: Path,
    file_name: str = "CODEBOARDING",
) -> Path:
    """Write the digest to ``<output_dir>/<file_name>.md`` and return the path.

    Why CODEBOARDING.md (not AGENTS.md): AGENTS.md is user-owned and overwriting
    it would be a data-loss incident. The CodeBoarding-generated digest gets a
    distinct filename; discoverability from AGENTS.md is handled separately via
    ``install_into_repo`` (non-destructive ``@`` import).
    """
    path = output_dir / f"{file_name}.md"
    path.write_text(generate_agents_md(root, sub_analyses, project=project), encoding="utf-8")
    return path
