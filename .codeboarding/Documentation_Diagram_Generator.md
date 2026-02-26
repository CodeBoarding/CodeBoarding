```mermaid
graph LR
    Output_Dispatcher["Output Dispatcher"]
    Markdown_Engine["Markdown Engine"]
    Sphinx_Engine["Sphinx Engine"]
    HTML_Visualization_Engine["HTML Visualization Engine"]
    Diagram_Synthesis_Engine["Diagram Synthesis Engine"]
    Graph_Data_Processor["Graph Data Processor"]
    Template_Manager["Template Manager"]
    Output_Dispatcher -- "Routes analysis data to" --> Markdown_Engine
    Output_Dispatcher -- "Routes analysis data to" --> HTML_Visualization_Engine
    Markdown_Engine -- "Requests visual definitions from" --> Diagram_Synthesis_Engine
    HTML_Visualization_Engine -- "Consumes formatted graph data from" --> Graph_Data_Processor
    HTML_Visualization_Engine -- "Requests visual definitions from" --> Diagram_Synthesis_Engine
    Sphinx_Engine -- "Utilizes shared assets from" --> Template_Manager
    Graph_Data_Processor -- "Feeds processed JSON to" --> HTML_Visualization_Engine
    Output_Dispatcher -- "Maintains registry of" --> Sphinx_Engine
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Transforms the processed analysis data and insights into user-friendly documentation formats (e.g., Markdown, HTML) and generates visual representations like architectural diagrams.

### Output Dispatcher
Acts as the central routing hub that receives structured analysis data from the Orchestrator and delegates it to the appropriate format engine based on user configuration.


**Related Classes/Methods**:

- `repos.codeboarding.output.OutputDispatcher`
- `repos.codeboarding.output.GeneratorRegistry`


### Markdown Engine
Synthesizes GitHub‑flavored Markdown and MDX documentation, handling front‑matter generation and embedding Mermaid.js strings for static rendering.


**Related Classes/Methods**:

- `repos.codeboarding.output.MarkdownOutputGenerator`
- `repos.codeboarding.output.MDXGenerator`
- `repos.codeboarding.output.FrontMatterHandler`


### Sphinx Engine
Transforms analysis results into ReStructuredText (RST) and Sphinx‑compatible directives to ensure compatibility with professional technical manual pipelines.


**Related Classes/Methods**:

- `repos.codeboarding.output.SphinxOutputGenerator`
- `repos.codeboarding.output.RSTDirectiveHandler`


### HTML Visualization Engine
Assembles interactive web reports using HTML/CSS templates and consumes processed graph structures for dynamic visual exploration.


**Related Classes/Methods**:

- `repos.codeboarding.output.HTMLReportGenerator`
- `repos.codeboarding.output.WebViewRenderer`


### Diagram Synthesis Engine
Generates visual definitions (Mermaid, Cytoscape) from structured architectural data, serving as a shared utility for both static and interactive outputs.


**Related Classes/Methods**:

- `repos.codeboarding.output.DiagramGenerator`
- `repos.codeboarding.output.MermaidGenerator`


### Graph Data Processor
Normalizes node and edge data into formats compatible with interactive graph libraries (e.g., Cytoscape JSON).


**Related Classes/Methods**:

- `repos.codeboarding.output.GraphDataProcessor`
- `repos.codeboarding.output.CytoscapeDataFormatter`


### Template Manager
Manages shared assets including CSS, HTML templates, and front‑matter configurations used across different output formats.


**Related Classes/Methods**:

- `repos.codeboarding.output.TemplateManager`
- `repos.codeboarding.output.AssetLoader`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)