```mermaid
graph LR
    Core_Rendering_Engine_Diagram_Orchestrator["Core Rendering Engine & Diagram Orchestrator"]
    Web_Native_Document_Generators_Markdown_MDX_["Web-Native Document Generators (Markdown/MDX)"]
    Sphinx_Ecosystem_Generator_RST_["Sphinx Ecosystem Generator (RST)"]
    Web_Native_Document_Generators_Markdown_MDX_ -- "Delegates diagram serialization and string sanitization" --> Core_Rendering_Engine_Diagram_Orchestrator
    Sphinx_Ecosystem_Generator_RST_ -- "Consumes semantic mapping and sanitization utilities" --> Core_Rendering_Engine_Diagram_Orchestrator
    Sphinx_Ecosystem_Generator_RST_ -- "Reuses node resolution and metadata logic" --> Web_Native_Document_Generators_Markdown_MDX_
    click Core_Rendering_Engine_Diagram_Orchestrator href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Core_Rendering_Engine_Diagram_Orchestrator.md" "Details"
    click Web_Native_Document_Generators_Markdown_MDX_ href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Web_Native_Document_Generators_Markdown_MDX_.md" "Details"
    click Sphinx_Ecosystem_Generator_RST_ href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Sphinx_Ecosystem_Generator_RST_.md" "Details"
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Transforms the internal architectural graph into standard documentation formats like Markdown, MDX, and RST for integration into developer workflows.

### Core Rendering Engine & Diagram Orchestrator [[Expand]](./Core_Rendering_Engine_Diagram_Orchestrator.md)
The foundational layer that transforms graph nodes and edges into Mermaid.js syntax and handles semantic mapping of static analysis types to human-readable labels.


**Related Classes/Methods**:

- `output_generators.markdown.generated_mermaid_str`:9-40
- `static_analyzer.constants.NodeType.label`:123-125
- `utils.sanitize`:92-94
- `static_analyzer.graph.CallGraph._common_dot_prefix`:665-678



**Source Files:**

- [`output_generators/html.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py)
  - `output_generators.html.component_header_html` ([L155-L163](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py#L155-L163)) - Function
- [`output_generators/markdown.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py)
  - `output_generators.markdown.generated_mermaid_str` ([L9-L40](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L9-L40)) - Function
- [`output_generators/mdx.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py)
  - `output_generators.mdx.generated_mermaid_str` ([L8-L35](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L8-L35)) - Function
- [`static_analyzer/constants.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py)
  - `static_analyzer.constants.NodeType.label` ([L123-L125](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py#L123-L125)) - Method
- [`static_analyzer/graph.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/graph.py)
  - `static_analyzer.graph.CallGraph._common_dot_prefix` ([L665-L678](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/graph.py#L665-L678)) - Method
  - `static_analyzer.graph.CallGraph.__cluster_str` ([L681-L770](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/graph.py#L681-L770)) - Method
- [`utils.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingutils.py)
  - `utils.sanitize` ([L92-L94](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingutils.py#L92-L94)) - Function


### Web-Native Document Generators (Markdown/MDX) [[Expand]](./Web_Native_Document_Generators_Markdown_MDX_.md)
Manages the generation of documentation optimized for modern web environments like GitHub and Docusaurus, handling frontmatter and hierarchical component descriptions.


**Related Classes/Methods**:

- `output_generators.markdown.generate_markdown`:43-122
- `output_generators.mdx.generate_mdx`:52-158
- `output_generators.mdx.generate_frontmatter`:38-49
- `static_analyzer.constants.NodeType.from_name`:128-137



**Source Files:**

- [`output_generators/markdown.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py)
  - `output_generators.markdown.generate_markdown` ([L43-L122](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L43-L122)) - Function
  - `output_generators.markdown.generate_markdown_file` ([L125-L146](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L125-L146)) - Function
  - `output_generators.markdown.component_header` ([L149-L157](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L149-L157)) - Function
- [`output_generators/mdx.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py)
  - `output_generators.mdx.generate_frontmatter` ([L38-L49](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L38-L49)) - Function
  - `output_generators.mdx.generate_mdx` ([L52-L158](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L52-L158)) - Function
  - `output_generators.mdx.generate_mdx_file` ([L161-L183](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L161-L183)) - Function
  - `output_generators.mdx.component_header` ([L186-L194](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L186-L194)) - Function
- [`static_analyzer/constants.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py)
  - `static_analyzer.constants.ClusteringConfig` ([L58-L83](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py#L58-L83)) - Class
  - `static_analyzer.constants.NodeType.from_name` ([L128-L137](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py#L128-L137)) - Method


### Sphinx Ecosystem Generator (RST) [[Expand]](./Sphinx_Ecosystem_Generator_RST_.md)
Specialized generator for the Python-centric Sphinx documentation ecosystem, implementing RST syntax and directive-based diagram embedding.


**Related Classes/Methods**:

- `output_generators.sphinx.generate_rst`:46-155
- `output_generators.sphinx.component_header`:186-197
- `output_generators.sphinx.generated_mermaid_str`:8-43



**Source Files:**

- [`output_generators/sphinx.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py)
  - `output_generators.sphinx.generated_mermaid_str` ([L8-L43](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L8-L43)) - Function
  - `output_generators.sphinx.generate_rst` ([L46-L155](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L46-L155)) - Function
  - `output_generators.sphinx.generate_rst_file` ([L158-L183](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L158-L183)) - Function
  - `output_generators.sphinx.component_header` ([L186-L197](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L186-L197)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)