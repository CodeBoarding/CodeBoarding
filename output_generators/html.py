import os
import re
from pathlib import Path
from typing import List, Dict, Any
import json

from agents.agent_responses import AnalysisInsights
from output_generators import sanitize
from utils import contains_json


def generate_cytoscape_data(analysis: AnalysisInsights, linked_files: List[Path], repo_ref: str, project: str,
                            demo=False) -> Dict[str, Any]:
    """Generate Cytoscape.js compatible data structure"""
    elements = []

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
                node_data['data'][
                    'linkUrl'] = f"https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/{project}/{node_id}.html"

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

    cytoscape_data = generate_cytoscape_data(insights, linked_files, repo_ref, project, demo)
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

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CodeBoarding Analysis - {project}</title>
    <!-- Load dagre first, then cytoscape, then cytoscape-dagre -->
    <script src="https://unpkg.com/dagre@0.8.5/dist/dagre.min.js"></script>
    <script src="https://unpkg.com/cytoscape@3.23.0/dist/cytoscape.min.js"></script>
    <script src="https://unpkg.com/cytoscape-dagre@2.4.0/cytoscape-dagre.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 0 auto;
            padding: 20px;
            background-color: #f8f9fa;
        }}
        
        .component {{
            border-left: 3px solid #6c757d;
            padding-left: 15px;
            margin-bottom: 20px;
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .component h3 {{
            color: #495057;
            margin-top: 0;
        }}
        
        code {{
            background-color: #e9ecef;
            padding: 2px 4px;
            border-radius: 3px;
            font-family: monospace;
        }}
        
        #cy {{
            width: 100%;
            height: 600px;
            border: 1px solid #dee2e6;
            margin: 20px 0;
            border-radius: 8px;
            background-color: #ffffff;
        }}
        
        .badges {{
            margin: 20px 0;
        }}
        
        .badge {{
            display: inline-block;
            margin-right: 10px;
        }}
        
        .badge img {{
            vertical-align: middle;
        }}
        
        .references {{
            list-style: none;
            padding-left: 0;
            margin: 10px 0;
        }}
        
        .references li {{
            margin: 4px 0;
        }}
        
        .diagram-controls {{
            margin: 10px 0;
            text-align: center;
        }}
        
        .diagram-controls button {{
            margin: 0 5px;
            padding: 8px 16px;
            background: #6c757d;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }}
        
        .diagram-controls button:hover {{
            background: #495057;
        }}
    </style>
</head>
<body>
    <h1>CodeBoarding Analysis{' - ' + project if project else ''}</h1>
    
    <div class="badges">
        <a href="https://github.com/CodeBoarding/GeneratedOnBoardings" class="badge">
            <img src="https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square" alt="Generated by CodeBoarding">
        </a>
        <a href="https://www.codeboarding.org/demo" class="badge">
            <img src="https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square" alt="Try our Demo">
        </a>
        <a href="mailto:contact@codeboarding.org" class="badge">
            <img src="https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square" alt="Contact us">
        </a>
    </div>
    
    <div class="diagram-controls">
        <button onclick="resetLayout()">Reset Layout</button>
        <button onclick="fitToView()">Fit to View</button>
        <button onclick="exportImage()">Export PNG</button>
    </div>
    
    <div id="cy"></div>
    
    <h2>Details</h2>
    <p>{insights.description}</p>
    
    {components_html}
    
    <h3><a href="https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq">FAQ</a></h3>
    
    <script>
        // Wait for all scripts to load before initializing
        document.addEventListener('DOMContentLoaded', function() {{
            // Check if all required libraries are loaded
            if (typeof cytoscape === 'undefined') {{
                console.error('Cytoscape is not loaded');
                document.getElementById('cy').innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">Error loading diagram. Please refresh the page.</div>';
                return;
            }}
            
            if (typeof dagre === 'undefined') {{
                console.error('Dagre is not loaded');
                document.getElementById('cy').innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">Error loading diagram. Please refresh the page.</div>';
                return;
            }}
            
            if (typeof cytoscapeDagre === 'undefined') {{
                console.error('Cytoscape-dagre extension is not loaded');
                document.getElementById('cy').innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">Error loading diagram. Please refresh the page.</div>';
                return;
            }}
            
            // Register the dagre extension
            try {{
                cytoscape.use(cytoscapeDagre);
                console.log('Dagre extension registered successfully');
            }} catch (e) {{
                console.error('Failed to register dagre extension:', e);
                document.getElementById('cy').innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">Error loading diagram. Please refresh the page.</div>';
                return;
            }}
            
            const cytoscapeData = {cytoscape_json};
            
            try {{
                const cy = cytoscape({{
                    container: document.getElementById('cy'),
                    
                    elements: cytoscapeData.elements,
                    
                    style: [
                        {{
                            selector: 'node',
                            style: {{
                                'background-color': '#f8f9fa',
                                'label': 'data(label)',
                                'text-valign': 'center',
                                'text-halign': 'center',
                                'color': '#495057',
                                'text-wrap': 'wrap',
                                'font-size': '11px',
                                'font-weight': '500',
                                'width': 'label',
                                'height': 'label',
                                'padding': '15px',
                                'shape': 'roundrectangle',
                                'border-width': 2,
                                'border-color': '#dee2e6'
                            }}
                        }},
                        {{
                            selector: 'node[hasLink = true]',
                            style: {{
                                'background-color': '#e9ecef',
                                'border-color': '#6c757d',
                                'cursor': 'pointer',
                                'border-width': 3
                            }}
                        }},
                        {{
                            selector: 'node:hover',
                            style: {{
                                'background-color': '#dee2e6',
                                'border-color': '#495057',
                                'border-width': 3
                            }}
                        }},
                        {{
                            selector: 'edge',
                            style: {{
                                'width': 2,
                                'line-color': '#adb5bd',
                                'target-arrow-color': '#adb5bd',
                                'target-arrow-shape': 'triangle',
                                'curve-style': 'bezier',
                                'label': 'data(label)',
                                'font-size': '10px',
                                'color': '#6c757d',
                                'text-rotation': 'autorotate',
                                'text-margin-y': -10,
                                'text-background-color': '#ffffff',
                                'text-background-opacity': 0.8,
                                'text-background-padding': '2px',
                                'text-background-shape': 'roundrectangle'
                            }}
                        }}
                    ],
                    
                    layout: {{
                        name: 'dagre',
                        directed: true,
                        padding: 30,
                        rankDir: 'LR',
                        nodeSep: 80,
                        edgeSep: 20,
                        rankSep: 150
                    }}
                }});
                
                // Apply dagre layout after cytoscape is initialized
                cy.layout({{
                    name: 'dagre',
                    directed: true,
                    padding: 30,
                    rankDir: 'LR',
                    nodeSep: 80,
                    edgeSep: 20,
                    rankSep: 150
                }}).run();
                
                // Add click handler for nodes with links
                cy.on('tap', 'node[hasLink = true]', function(evt) {{
                    const node = evt.target;
                    const linkUrl = node.data('linkUrl');
                    if (linkUrl) {{
                        window.open(linkUrl, '_blank');
                    }}
                }});
                
                // Add simple tooltip on hover
                cy.on('mouseover', 'node', function(evt) {{
                    const node = evt.target;
                    const description = node.data('description');
                    if (description) {{
                        // Simple tooltip implementation
                        const tooltip = document.createElement('div');
                        tooltip.innerHTML = description;
                        tooltip.style.cssText = `
                            position: fixed;
                            background: #333;
                            color: white;
                            padding: 8px;
                            border-radius: 4px;
                            font-size: 12px;
                            max-width: 300px;
                            z-index: 1000;
                            pointer-events: none;
                        `;
                        document.body.appendChild(tooltip);
                        
                        const updateTooltip = (e) => {{
                            tooltip.style.left = (e.clientX + 10) + 'px';
                            tooltip.style.top = (e.clientY + 10) + 'px';
                        }};
                        
                        document.addEventListener('mousemove', updateTooltip);
                        
                        node.on('mouseout', () => {{
                            document.removeEventListener('mousemove', updateTooltip);
                            if (tooltip.parentNode) {{
                                tooltip.parentNode.removeChild(tooltip);
                            }}
                        }});
                    }}
                }});
                
                // Make control functions globally available
                window.resetLayout = function() {{
                    cy.layout({{
                        name: 'dagre',
                        directed: true,
                        padding: 30,
                        rankDir: 'LR',
                        nodeSep: 80,
                        edgeSep: 20,
                        rankSep: 150
                    }}).run();
                }};
                
                window.fitToView = function() {{
                    cy.fit();
                }};
                
                window.exportImage = function() {{
                    const png64 = cy.png({{ scale: 2, full: true }});
                    const link = document.createElement('a');
                    link.download = 'diagram.png';
                    link.href = png64;
                    link.click();
                }};
                
            }} catch (error) {{
                console.error('Error initializing Cytoscape:', error);
                document.getElementById('cy').innerHTML = '<div style="padding: 20px; text-align: center; color: #666;">Error loading diagram. Please refresh the page.</div>';
            }}
        }});
    </script>
</body>
</html>"""

    return html_content


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
    p = Path("/home/ivan/StartUp/CodeBoarding/temp/2ee21687d49442ad9ec50b999cb5decf")
    jsons = list(p.rglob("*.json"))
    for file in jsons:
        if file.stem == "codeboarding_version":
            continue
        with open(file, 'r') as f:
            model = AnalysisInsights.model_validate_json(f.read())
            html_content = generate_html_file(file.stem, model, "django", "./", linked_files=jsons,
                                              temp_dir=Path("./"), demo=False)
