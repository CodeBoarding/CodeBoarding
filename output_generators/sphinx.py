import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from agents.agent_responses import AnalysisInsights
from output_generators import sanitize
from utils import contains_json


def generated_mermaid_str(analysis: AnalysisInsights, linked_files: List[Path], repo_ref: str, project: str,
                          demo=False) -> str:
    """
    Generate a Mermaid diagram representation in RST format.
    """
    lines = [".. mermaid::", "", "   graph LR"]

    # Define each component as a node
    for comp in analysis.components:
        node_id = sanitize(comp.name)
        # Show name in the node label
        label = f"{comp.name}"
        lines.append(f'      {node_id}["{label}"]')

    # Add relations as labeled edges
    for rel in analysis.components_relations:
        src_id = sanitize(rel.src_name)
        dst_id = sanitize(rel.dst_name)
        # Use the relation phrase as the edge label
        lines.append(f'      {src_id} -- "{rel.relation}" --> {dst_id}')

    # Linking to other files.
    for comp in analysis.components:
        node_id = sanitize(comp.name)
        if contains_json(node_id, linked_files):
            # Create a link to the component's details file
            if not demo:
                lines.append(f'      click {node_id} href "{repo_ref}/{node_id}.html" "Details"')
            else:
                # For demo, link to a static URL
                lines.append(
                    f'      click {node_id} href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/{project}/{node_id}.html" "Details"')

    return '\n'.join(lines)


def generate_rst(insights: AnalysisInsights, project: str = "", repo_ref="",
                 linked_files=None, demo=False, file_name: str = "") -> str:
    """
    Generate a RST document from an AnalysisInsights object.
    """
    linked_files = linked_files or []

    # Use file_name to create a better title, replacing underscores with spaces
    title = file_name.replace('_', ' ').title()
    title_underline = "=" * len(title)

    lines = [title, title_underline, ""]

    # Add diagram
    diagram_str = generated_mermaid_str(insights, repo_ref=repo_ref, linked_files=linked_files, project=project,
                                        demo=demo)
    lines.append(diagram_str)

    # Add CodeBoarding footer
    lines.append("")
    lines.append("| |codeboarding-badge| |demo-badge| |contact-badge|")
    lines.append("")
    lines.append(
        ".. |codeboarding-badge| image:: https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square")
    lines.append("   :target: https://github.com/CodeBoarding/CodeBoarding")
    lines.append(".. |demo-badge| image:: https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square")
    lines.append("   :target: https://www.codeboarding.org/demo")
    lines.append(
        ".. |contact-badge| image:: https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square")
    lines.append("   :target: mailto:contact@codeboarding.org")

    # Add project details
    lines.append("")
    lines.append("Details")
    lines.append("-------")
    lines.append("")
    lines.append(insights.description)
    lines.append("")

    # Add component details
    root_dir = os.getenv('REPO_ROOT') + "/" + project

    for comp in insights.components:
        lines.append(component_header(comp.name, linked_files))
        lines.append("")
        lines.append(comp.description)
        lines.append("")

        if comp.referenced_source_code:
            lines.append("**Related Classes/Methods**:")
            lines.append("")

            for reference in comp.referenced_source_code:
                print(reference.reference_file, root_dir)
                if not reference.reference_file:
                    continue
                if not reference.reference_file.startswith(root_dir):
                    lines.append(f"* {str(reference).replace('`', '')}")
                    continue
                url = "/".join(repo_ref.split("/")[:7])
                ref_url = url + reference.reference_file.split(root_dir)[1]
                if reference.reference_start_line is not None and reference.reference_end_line is not None and (
                        not (reference.reference_start_line <= reference.reference_end_line <= 0 or
                             reference.reference_start_line == reference.reference_end_line)):
                    ref_url += f"#L{reference.reference_start_line}-L{reference.reference_end_line}"
                lines.append(f"* `{str(reference).replace('`', '')} <{ref_url}>`_")
            lines.append("")
        else:
            lines.append("**Related Classes/Methods**: *None*")
            lines.append("")

    # Add FAQ link
    lines.append("")
    lines.append("FAQ")
    lines.append("---")
    lines.append("")
    lines.append(
        "`See the FAQ <https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq>`_")

    return "\n".join(lines)


def generate_rst_file(file_name: str, insights: AnalysisInsights, project: str, repo_ref: str,
                      linked_files, temp_dir: Path, demo: bool = False) -> Path:
    """
    Generate a RST file with the given insights and save it to the specified directory.
    """
    content = generate_rst(insights, project=project, repo_ref=repo_ref,
                           linked_files=linked_files, demo=demo, file_name=file_name)
    rst_file = temp_dir / f"{file_name}.rst"
    with open(rst_file, "w") as f:
        f.write(content)
    return rst_file


def component_header(component_name: str, link_files: List[Path]) -> str:
    """
    Generate a header for a component with its name and a reference to its details.
    """
    sanitized_name = sanitize(component_name)
    header_text = component_name
    header_underline = "^" * len(header_text)

    if contains_json(sanitized_name, link_files):
        return f"{header_text}\n{header_underline}\n\n:ref:`Expand <{sanitized_name}>`"
    else:
        return f"{header_text}\n{header_underline}"


if __name__ == "__main__":
    load_dotenv()
    file = "/home/ivan/StartUp/CodeBoarding/analysis.json"
    with open(file, 'r') as f:
        example_insights = AnalysisInsights.model_validate_json(f.read())
    rst_content = generate_rst_file("overview", insights=example_insights, project="aestetik",
                                    repo_ref="www.github.com/CodeBoarding",
                                    linked_files=[], temp_dir=Path("./"))
