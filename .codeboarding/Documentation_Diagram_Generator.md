```mermaid
graph LR
    Output_Orchestration_Dispatcher["Output Orchestration Dispatcher"]
    Visual_Model_Generator["Visual Model Generator"]
    Interactive_HTML_Provider["Interactive HTML Provider"]
    Static_Markdown_Provider["Static Markdown Provider"]
    Sphinx_Documentation_Provider["Sphinx Documentation Provider"]
    Output_Orchestration_Dispatcher -- "dispatches to" --> Interactive_HTML_Provider
    Output_Orchestration_Dispatcher -- "dispatches to" --> Static_Markdown_Provider
    Output_Orchestration_Dispatcher -- "dispatches to" --> Sphinx_Documentation_Provider
    Visual_Model_Generator -- "supplies data to" --> Interactive_HTML_Provider
    Visual_Model_Generator -- "supplies data to" --> Static_Markdown_Provider
    Visual_Model_Generator -- "supplies data to" --> Sphinx_Documentation_Provider
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Transforms the processed analysis data and insights into user-friendly documentation formats (e.g., Markdown, HTML) and generates visual representations like architectural diagrams.

### Output Orchestration Dispatcher
The central controller that receives the final analysis manifest and routes data to the appropriate output generators based on user-defined configurations (e.g., `--format html,markdown`).


**Related Classes/Methods**:

- `repos.codeboarding.output.MultiFormatDocumenter`
- `repos.codeboarding.output.OutputGenerator`


### Visual Model Generator
The engine responsible for translating static analysis relationships into graph‑based syntax. It generates the raw Mermaid.js strings and Cytoscape JSON structures used by the providers.


**Related Classes/Methods**:

- `repos.codeboarding.output.DiagramGenerator`
- `repos.codeboarding.output.MermaidGenerator`


### Interactive HTML Provider
Generates standalone, interactive web documentation. It embeds Cytoscape.js for dynamic diagram manipulation and manages CSS/JS assets for the UI.


**Related Classes/Methods**:

- `repos.codeboarding.output.html.HTMLOutputGenerator`
- `repos.codeboarding.output.html.CytoscapeGenerator`
- `repos.codeboarding.output.html.TemplateEngine`


### Static Markdown Provider
Produces Markdown and MDX files optimized for static hosting (GitHub, Docusaurus). It embeds Mermaid.js code blocks for native rendering.


**Related Classes/Methods**:

- `repos.codeboarding.output.markdown.MarkdownOutputGenerator`
- `repos.codeboarding.output.markdown.MdxOutputGenerator`


### Sphinx Documentation Provider
A specialized generator that produces ReStructuredText (RST) files, allowing the tool's output to be seamlessly integrated into existing Python Sphinx documentation suites.


**Related Classes/Methods**:

- `repos.codeboarding.output.sphinx.SphinxOutputGenerator`
- `repos.codeboarding.output.sphinx.RstGenerator`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)