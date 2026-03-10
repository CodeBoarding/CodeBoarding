```mermaid
graph LR
    HTMLOutputGenerator["HTMLOutputGenerator"]
    TemplateRenderer["TemplateRenderer"]
    CytoscapeRenderer["CytoscapeRenderer"]
    HTMLOutputGenerator -- "utilizes" --> TemplateRenderer
    HTMLOutputGenerator -- "integrates" --> CytoscapeRenderer
    TemplateRenderer -- "processes rendering requests from" --> HTMLOutputGenerator
    CytoscapeRenderer -- "receives graph data from" --> HTMLOutputGenerator
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Produces interactive HTML documentation, rendering analysis insights and diagrams into a web-friendly format. It utilizes templates and potentially libraries like Cytoscape for dynamic visualizations.

### HTMLOutputGenerator
Produces interactive HTML documentation, rendering analysis insights and diagrams into a web-friendly format. It utilizes templates and potentially libraries like Cytoscape for dynamic visualizations.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.output.html.generate_html`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.output.html.generate_html_file`</a>


### TemplateRenderer
A utility component responsible for applying HTML templates to data. It abstracts the templating logic, allowing HTMLGenerator to focus on content assembly.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.output.html_template.populate_html_template`</a>


### CytoscapeRenderer
Specializes in generating interactive graph visualizations using the Cytoscape.js library. It transforms graph-like analysis data into a format suitable for dynamic display within the HTML output.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.output.html.generate_cytoscape_data`</a>




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)