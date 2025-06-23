import os
import re
from pathlib import Path
from typing import List

from agents.agent_responses import AnalysisInsights
from utils import contains_json


def generated_mermaid_str(analysis: AnalysisInsights, linked_files: List[Path], repo_ref: str, project: str,
                          demo=False) -> str:
    lines = ["```mermaid", "graph LR"]

    # 1. Define each component as a node, including its description
    for comp in analysis.components:
        node_id = sanitize(comp.name)
        # Show name and short description in the node label
        label = f"{comp.name}"
        lines.append(f'    {node_id}["{label}"]')

    # 2. Add relations as labeled edges
    for rel in analysis.components_relations:
        src_id = sanitize(rel.src_name)
        dst_id = sanitize(rel.dst_name)
        # Use the relation phrase as the edge label
        lines.append(f'    {src_id} -- "{rel.relation}" --> {dst_id}')
    # Linking to other files.
    for comp in analysis.components:
        node_id = sanitize(comp.name)
        if contains_json(node_id, linked_files):
            # Create a link to the component's details file
            if not demo:
                lines.append(f'    click {node_id} href "{repo_ref}/{node_id}.md" "Details"')
            else:
                # For demo, link to a static URL
                lines.append(
                    f'    click {node_id} href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/{project}/{node_id}.md" "Details"')
    lines.append("```")
    return '\n'.join(lines)


def generate_markdown(insights: AnalysisInsights, project: str = "", repo_ref="",
                      linked_files=None, demo=False) -> str:
    """
    Generate a Mermaid 'graph LR' diagram from an AnalysisInsights object.
    """

    mermaid_str = generated_mermaid_str(insights, repo_ref=repo_ref, linked_files=linked_files, project=project,
                                        demo=demo)

    lines = [mermaid_str,
             "\n[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/GeneratedOnBoardings)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/demo)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)"]

    detail_lines = ["\n## Details\n", f"{insights.description}\n"]

    root_dir = os.getenv('REPO_ROOT') + "/" + project

    for comp in insights.components:
        detail_lines.append(component_header(comp.name, linked_files))
        detail_lines.append(f"{comp.description}")
        if comp.referenced_source_code:
            qn_list = []
            for reference in comp.referenced_source_code:
                print(reference.reference_file, root_dir)
                if reference.reference_start_line is None or reference.reference_end_line is None:
                    qn_list.append(f"{reference.llm_str()}")
                    continue
                if not reference.reference_file:
                    continue
                if not reference.reference_file.startswith(root_dir):
                    qn_list.append(f"{reference.llm_str()}")
                    continue
                ref_url = repo_ref + reference.reference_file.split(root_dir)[1] \
                          + f"#L{reference.reference_start_line}-L{reference.reference_end_line}"
                qn_list.append(
                    f'<a href="{ref_url}" target="_blank" rel="noopener noreferrer">{reference.llm_str()}</a>')
            # Join the list into an unordered markdown list, without the leading dash
            references = ""
            for item in qn_list:
                references += f"- {item}\n"

            detail_lines.append(f"\n\n**Related Classes/Methods**:\n\n{references}")
        else:
            detail_lines.append(f"\n\n**Related Classes/Methods**: _None_")
        detail_lines.append("")  # blank line between components

    detail_lines.append(
        "\n\n### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)")
    return "\n".join(lines + detail_lines)


def generate_markdown_file(file_name: str, insights: AnalysisInsights, project: str, repo_ref: str,
                           linked_files, temp_dir: Path, demo: bool = False) -> Path:
    content = generate_markdown(insights, project=project, repo_ref=repo_ref,
                                linked_files=linked_files, demo=demo)
    markdown_file = temp_dir / f"{file_name}.md"
    with open(markdown_file, "w") as f:
        f.write(content)
    return markdown_file


def component_header(component_name: str, link_files: List[Path]) -> str:
    """
    Generate a header for a component with its name and a link to its details.
    """
    sanitized_name = sanitize(component_name)
    if contains_json(sanitized_name, link_files):
        return f"### {component_name} [[Expand]](./{sanitized_name}.md)"
    else:
        return f"### {component_name}"


def sanitize(name: str) -> str:
    # Replace non-alphanumerics with underscores so IDs are valid Mermaid identifiers
    return re.sub(r'\W+', '_', name)
