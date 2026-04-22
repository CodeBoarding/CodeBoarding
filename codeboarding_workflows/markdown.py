import json
from pathlib import Path

from diagram_analysis.analysis_json import build_id_to_name_map, parse_unified_analysis
from output_generators.markdown import generate_markdown_file
from repo_utils import get_branch
from utils import sanitize


def generate_markdown_docs(
    repo_name: str,
    repo_path: Path,
    repo_url: str,
    analysis_path: Path,
    output_dir: Path,
    demo_mode: bool = False,
) -> None:
    target_branch = get_branch(repo_path)
    repo_ref = f"{repo_url}/blob/{target_branch}/"

    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    root_analysis, sub_analyses = parse_unified_analysis(data)

    root_expanded = set(sub_analyses.keys())
    generate_markdown_file(
        "on_boarding",
        root_analysis,
        repo_name,
        repo_ref=repo_ref,
        expanded_components=root_expanded,
        temp_dir=output_dir,
        demo=demo_mode,
    )

    id_to_name = build_id_to_name_map(root_analysis, sub_analyses)
    for comp_id, sub_analysis in sub_analyses.items():
        sub_expanded = {c.component_id for c in sub_analysis.components if c.component_id in sub_analyses}
        comp_name = id_to_name.get(comp_id, comp_id)
        fname = sanitize(comp_name)
        generate_markdown_file(
            fname,
            sub_analysis,
            repo_name,
            repo_ref=repo_ref,
            expanded_components=sub_expanded,
            temp_dir=output_dir,
            demo=demo_mode,
        )
