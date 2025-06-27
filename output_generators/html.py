import os
from pathlib import Path
from typing import List, Dict, Any
import json

from agents.agent_responses import AnalysisInsights
from output_generators import sanitize
from output_generators.html_template import populate_html_template
from utils import contains_json


def generate_cytoscape_data(analysis: AnalysisInsights, linked_files: List[Path], project: str,
                            demo=False) -> Dict[str, Any]:
    """Generate Cytoscape.js compatible data structure"""
    elements: List[Dict] = []

    # Add nodes (components)
    component_ids = set()
    for comp in analysis.components:
        node_id = sanitize(comp.name)
        component_ids.add(node_id)

        # Determine if component has linked file for styling
        has_link = contains_json(node_id, linked_files)

        node_data = {
            'data': {
                'id': node_id,
                'label': comp.name,
                'description': comp.description,
                'hasLink': has_link
            }
        }

        # Add link URL if component has linked file
        if has_link:
            if not demo:
                node_data['data']['linkUrl'] = f"./{node_id}.html"
            else:
                node_data['data']['linkUrl'] = \
                    f"https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/{project}/{node_id}.html"

        elements.append(node_data)

    # Add edges (relations) - only if both source and target nodes exist
    edge_count = 0
    for rel in analysis.components_relations:
        src_id = sanitize(rel.src_name)
        dst_id = sanitize(rel.dst_name)

        # Only add edge if both source and destination nodes exist
        if src_id in component_ids and dst_id in component_ids:
            edge_data = {
                'data': {
                    'id': f'edge_{edge_count}',
                    'source': src_id,
                    'target': dst_id,
                    'label': rel.relation
                }
            }
            elements.append(edge_data)
            edge_count += 1
        else:
            print(
                f"Warning: Skipping edge from '{rel.src_name}' to '{rel.dst_name}' - one or both nodes don't exist in components")

    return {'elements': elements}


def generate_html(insights: AnalysisInsights, project: str = "", repo_ref: str = "",
                  linked_files=None, demo=False) -> str:
    """
    Generate an HTML document with a Cytoscape.js diagram from an AnalysisInsights object.
    """

    cytoscape_data = generate_cytoscape_data(insights, linked_files, project, demo)
    cytoscape_json = json.dumps(cytoscape_data, indent=2)

    root_dir = os.getenv('REPO_ROOT') + "/" + project

    # Build component details HTML
    components_html = ""

    for comp in insights.components:
        component_id = sanitize(comp.name)

        # Build references HTML
        references_html = ""
        if comp.referenced_source_code:
            references_html = '<h4>Related Classes/Methods:</h4><ul class="references">'
            for reference in comp.referenced_source_code:
                if reference.reference_start_line is None or reference.reference_end_line is None:
                    references_html += f'<li><code>{reference.llm_str()}</code></li>'
                    continue
                if not reference.reference_file:
                    references_html += f'<li><code>{reference.llm_str()}</code></li>'
                    continue
                if not reference.reference_file.startswith(root_dir):
                    references_html += f'<li><code>{reference.llm_str()}</code></li>'
                    continue
                ref_url = repo_ref + reference.reference_file.split(root_dir)[1] \
                          + f"#L{reference.reference_start_line}-L{reference.reference_end_line}"
                references_html += f'<li><a href="{ref_url}" target="_blank" rel="noopener noreferrer"><code>{reference.llm_str()}</code></a></li>'
            references_html += "</ul>"
        else:
            references_html = "<h4>Related Classes/Methods:</h4><p><em>None</em></p>"

        # Check if there's a linked file for this component
        expand_link = ""
        if contains_json(component_id, linked_files):
            expand_link = f' <a href="./{component_id}.html">[Expand]</a>'

        components_html += f"""
        <div class="component">
            <h3 id="{component_id}">{comp.name}{expand_link}</h3>
            <p>{comp.description}</p>
            {references_html}
        </div>
        """

    return populate_html_template(components_html=components_html, cytoscape_json=cytoscape_json, insights=insights,
                                  project=project)


def generate_html_file(file_name: str, insights: AnalysisInsights, project: str, repo_ref: str,
                       linked_files, temp_dir: Path, demo: bool = False) -> Path:
    """
    Generate an HTML file with the analysis insights.
    """
    content = generate_html(insights, project=project, repo_ref=repo_ref,
                            linked_files=linked_files, demo=demo)
    html_file = temp_dir / f"{file_name}.html"
    with open(html_file, "w", encoding='utf-8') as f:
        f.write(content)
    return html_file


def component_header_html(component_name: str, link_files: List[Path]) -> str:
    """
    Generate an HTML header for a component with its name and a link to its details.
    """
    sanitized_name = sanitize(component_name)
    if contains_json(sanitized_name, link_files):
        return f'<h3 id="{sanitized_name}">{component_name} <a href="./{sanitized_name}.html">[Expand]</a></h3>'
    else:
        return f'<h3 id="{sanitized_name}">{component_name}</h3>'


if __name__ == "__main__":
    import dotenv

    dotenv.load_dotenv()
    p = Path("/home/ivan/StartUp/CodeBoarding/temp/483eb9f6c8fd46f1a0f9dc6d40da4bbd")
    jsons = list(p.rglob("*.json"))
    for file in jsons:
        if file.stem == "codeboarding_version":
            continue
        with open(file, 'r') as f:
            model = AnalysisInsights.model_validate_json(f.read())
            html_content = generate_html_file(file.stem, model, "django", "./", linked_files=jsons,
                                              temp_dir=Path("./"), demo=False)
