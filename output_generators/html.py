import os
from pathlib import Path
from typing import Dict, Any
import json

from agents.agent_responses import AnalysisInsights
from utils import sanitize
from output_generators.html_template import populate_html_template


def generate_cytoscape_data(
    analysis: AnalysisInsights, expanded_components: set[str], project: str, demo=False
) -> Dict[str, Any]:
    """Generate Cytoscape.js compatible data structure"""
    elements: list[Dict] = []

    # Add nodes (components)
    component_ids = set()
    for comp in analysis.components:
        node_key = sanitize(comp.name)
        component_ids.add(node_key)

        # Determine if component has linked file for styling
        has_link = comp.component_id in expanded_components

        node_data = {"data": {"id": node_key, "label": comp.name, "description": comp.description, "hasLink": has_link}}

        # Add link URL if component has linked file
        if has_link:
            if not demo:
                node_data["data"]["linkUrl"] = f"./{node_key}.html"
            else:
                node_data["data"][
                    "linkUrl"
                ] = f"https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/{project}/{node_key}.html"

        elements.append(node_data)

    # Add edges (relations) - only if both source and target nodes exist
    edge_count = 0
    for rel in analysis.components_relations:
        src_key = sanitize(rel.src_name)
        dst_key = sanitize(rel.dst_name)

        # Only add edge if both source and destination nodes exist
        if src_key in component_ids and dst_key in component_ids:
            edge_data = {
                "data": {"id": f"edge_{edge_count}", "source": src_key, "target": dst_key, "label": rel.relation}
            }
            elements.append(edge_data)
            edge_count += 1
        else:
            print(
                f"Warning: Skipping edge from '{rel.src_name}' to '{rel.dst_name}' - one or both nodes don't exist in components"
            )

    return {"elements": elements}


def generate_html(
    insights: AnalysisInsights,
    project: str = "",
    repo_ref: str = "",
    expanded_components: set[str] | None = None,
    demo=False,
) -> str:
    """
    Generate an HTML document with a Cytoscape.js diagram from an AnalysisInsights object.
    """
    expanded_components = expanded_components or set()

    cytoscape_data = generate_cytoscape_data(insights, expanded_components, project, demo)
    cytoscape_json = json.dumps(cytoscape_data, indent=2)

    repo_root = os.getenv("REPO_ROOT")
    root_dir = os.path.join(repo_root, project) if repo_root else project

    # Build component details HTML
    components_html = ""

    for comp in insights.components:
        component_id = sanitize(comp.name)

        # Build references HTML
        references_html = ""
        if comp.key_entities:
            references_html = '<h4>Related Classes/Methods:</h4><ul class="references">'
            for reference in comp.key_entities:
                if reference.reference_start_line is None or reference.reference_end_line is None:
                    references_html += f"<li><code>{reference.llm_str()}</code></li>"
                    continue
                if not reference.reference_file:
                    references_html += f"<li><code>{reference.llm_str()}</code></li>"
                    continue
                if not reference.reference_file.startswith(root_dir):
                    references_html += f"<li><code>{reference.llm_str()}</code></li>"
                    continue
                # Handle case when root_dir is empty or reference file doesn't start with root_dir
                if root_dir and reference.reference_file.startswith(root_dir):
                    relative_path = reference.reference_file.split(root_dir)[1]
                else:
                    relative_path = reference.reference_file
                ref_url = (
                    repo_ref + relative_path + f"#L{reference.reference_start_line}-L{reference.reference_end_line}"
                )
                references_html += f'<li><a href="{ref_url}" target="_blank" rel="noopener noreferrer"><code>{reference.llm_str()}</code></a></li>'
            references_html += "</ul>"
        else:
            references_html = "<h4>Related Classes/Methods:</h4><p><em>None</em></p>"

        # Check if there's a linked file for this component
        expand_link = ""
        if comp.component_id in expanded_components:
            expand_link = f' <a href="./{component_id}.html">[Expand]</a>'

        components_html += f"""
        <div class="component">
            <h3 id="{component_id}">{comp.name}{expand_link}</h3>
            <p>{comp.description}</p>
            {references_html}
        </div>
        """

    return populate_html_template(
        components_html=components_html, cytoscape_json=cytoscape_json, insights=insights, project=project
    )


def generate_html_file(
    file_name: str,
    insights: AnalysisInsights,
    project: str,
    repo_ref: str,
    expanded_components: set[str],
    temp_dir: Path,
    demo: bool = False,
) -> Path:
    """
    Generate an HTML file with the analysis insights.
    """
    content = generate_html(
        insights, project=project, repo_ref=repo_ref, expanded_components=expanded_components, demo=demo
    )
    html_file = temp_dir / f"{file_name}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(content)
    return html_file


def component_header_html(component_name: str, component_id: str, expanded_components: set[str]) -> str:
    """
    Generate an HTML header for a component with its name and a link to its details.
    """
    sanitized_name = sanitize(component_name)
    if component_id in expanded_components:
        return f'<h3 id="{sanitized_name}">{component_name} <a href="./{sanitized_name}.html">[Expand]</a></h3>'
    else:
        return f'<h3 id="{sanitized_name}">{component_name}</h3>'
