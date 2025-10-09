```mermaid
graph LR
    github_action["github_action"]
    output_generators_markdown["output_generators.markdown"]
    output_generators_html["output_generators.html"]
    output_generators_mdx["output_generators.mdx"]
    output_generators_sphinx["output_generators.sphinx"]
    Unclassified["Unclassified"]
    github_action -- "delegates to" --> output_generators_markdown
    github_action -- "delegates to" --> output_generators_html
    github_action -- "delegates to" --> output_generators_mdx
    github_action -- "delegates to" --> output_generators_sphinx
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The Output Generation Engine subsystem transforms validated architectural insights into various documentation formats and integrates with external systems like GitHub Actions. The main flow involves the `github_action` component acting as the primary entry point, orchestrating the selection and execution of specific output format generators (Markdown, HTML, MDX, Sphinx/RST) based on user configuration. Its purpose is to provide diverse documentation outputs and enable seamless integration within a CI/CD environment.

### github_action
Acts as the primary entry point for initiating output generation within a GitHub Actions workflow. It orchestrates the selection and execution of specific output format generators based on user configuration.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/maingithub_action.py" target="_blank" rel="noopener noreferrer">`github_action`</a>


### output_generators.markdown
Generates documentation in Markdown format.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainoutput_generators/markdown.py" target="_blank" rel="noopener noreferrer">`output_generators.markdown`</a>


### output_generators.html
Generates documentation in HTML format.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainoutput_generators/html.py" target="_blank" rel="noopener noreferrer">`output_generators.html`</a>


### output_generators.mdx
Generates documentation in MDX (Markdown with JSX) format.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainoutput_generators/mdx.py" target="_blank" rel="noopener noreferrer">`output_generators.mdx`</a>


### output_generators.sphinx
Generates documentation in reStructuredText (RST) format, specifically tailored for Sphinx documentation projects, including embedded Mermaid diagrams.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainoutput_generators/sphinx.py" target="_blank" rel="noopener noreferrer">`output_generators.sphinx`</a>


### Unclassified
Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)


**Related Classes/Methods**: _None_



### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
