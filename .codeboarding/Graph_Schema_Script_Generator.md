```mermaid
graph LR
    Cytoscape_Schema_Transformer["Cytoscape Schema Transformer"]
    Graph_Interaction_Layout_Engine["Graph Interaction & Layout Engine"]
    Connectivity_Edge_Resolver["Connectivity & Edge Resolver"]
    Cytoscape_Schema_Transformer -- "Injects serialized graph data into the runtime environment" --> Graph_Interaction_Layout_Engine
    Graph_Interaction_Layout_Engine -- "Defines data requirements for layout and interaction" --> Cytoscape_Schema_Transformer
    Connectivity_Edge_Resolver -- "Provides resolved graph topology for transformation" --> Cytoscape_Schema_Transformer
    Connectivity_Edge_Resolver -- "Influences visual grouping and hierarchy constraints" --> Graph_Interaction_Layout_Engine
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Translates internal architectural entities into the JSON format required by Cytoscape.js and generates the JavaScript logic for node styling, layout, and event handling.

### Cytoscape Schema Transformer
Translates internal architectural representations into the JSON format required by Cytoscape.js, preserving hierarchical structures.


**Related Classes/Methods**: _None_


**Source Files:**

- [`output_generators/html.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py)
  - `output_generators.html.generate_html` ([L59-L125](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py#L59-L125)) - Function
  - `output_generators.html.generate_html_file` ([L128-L152](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py#L128-L152)) - Function


### Graph Interaction & Layout Engine
Generates JavaScript logic for graph rendering, layout configuration, and user interaction handling within the generated HTML report.


**Related Classes/Methods**: _None_


**Source Files:**

- [`output_generators/html_template.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py)
  - `output_generators.html_template._generate_css_styles` ([L4-L86](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L4-L86)) - Function
  - `output_generators.html_template._generate_html_body` ([L89-L119](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L89-L119)) - Function


### Connectivity & Edge Resolver
Manages the mapping of static analysis relationships to visual edges, ensuring hierarchical connectivity is correctly represented.


**Related Classes/Methods**: _None_


**Source Files:**

- [`output_generators/html_template.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py)
  - `output_generators.html_template._generate_cytoscape_script` ([L314-L357](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L314-L357)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)