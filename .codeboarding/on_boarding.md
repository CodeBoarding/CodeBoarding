```mermaid
graph LR
    Orchestration_Workflow["Orchestration & Workflow"]
    Static_Code_Analyzer["Static Code Analyzer"]
    AI_Analysis_Engine["AI Analysis Engine"]
    Analysis_Persistence["Analysis Persistence"]
    Output_Generator["Output Generator"]
    Orchestration_Workflow -- "invokes analysis on" --> Static_Code_Analyzer
    Static_Code_Analyzer -- "returns raw graph data to" --> Orchestration_Workflow
    Orchestration_Workflow -- "consults and saves analysis to" --> Analysis_Persistence
    Analysis_Persistence -- "provides cached analysis to" --> Orchestration_Workflow
    Orchestration_Workflow -- "invokes with graph data" --> AI_Analysis_Engine
    AI_Analysis_Engine -- "returns high-level model to" --> Orchestration_Workflow
    Orchestration_Workflow -- "sends model for rendering to" --> Output_Generator
    click Orchestration_Workflow href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Orchestration_Workflow.md" "Details"
    click Static_Code_Analyzer href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Static_Code_Analyzer.md" "Details"
    click AI_Analysis_Engine href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/AI_Analysis_Engine.md" "Details"
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/GeneratedOnBoardings)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/demo)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Abstract Components Overview

### Orchestration & Workflow [[Expand]](./Orchestration_Workflow.md)
The central coordinator that manages the end-to-end analysis pipeline. It initiates static analysis, triggers the AI engine, coordinates with the persistence layer for caching and differential checks, and sends the final, validated results to the output generator.


**Related Classes/Methods**:

- `local_app.py`
- `github_action.py`


### Static Code Analyzer [[Expand]](./Static_Code_Analyzer.md)
Responsible for the initial, non-AI parsing of the source code. It uses AST-based techniques to build foundational data structures like call graphs and structure graphs, transforming raw code into a structured format that the AI engine can interpret.


**Related Classes/Methods**:

- `static_analyzer/pylint_analyze/call_graph_builder.py`
- `static_analyzer/pylint_analyze/structure_graph_builder.py`
- `static_analyzer/pylint_graph_transform.py`


### AI Analysis Engine [[Expand]](./AI_Analysis_Engine.md)
The cognitive core of the system. It is a multi-agent framework that interprets the data from the static analyzer. It uses a collection of specialized agents (e.g., Planner, Abstraction, Diff Analyzer) to collaboratively identify architectural patterns, understand component roles, and build a comprehensive model of the codebase.


**Related Classes/Methods**:

- `agents/agent.py`
- `agents/planner_agent.py`
- `agents/abstraction_agent.py`
- `agents/diff_analyzer.py`


### Analysis Persistence
Handles the serialization and deserialization of the analysis model to a storable format (JSON). This enables the caching of results, which is critical for performance and for supporting efficient incremental analysis by providing a baseline for comparison.


**Related Classes/Methods**:

- `diagram_analysis/analysis_json.py`


### Output Generator
The final stage in the pipeline. It consumes the rich, structured analysis model produced by the AI Engine and renders it into various human-readable formats, such as Markdown, HTML, and Sphinx documentation. Recent changes indicate a streamlining or simplification of its output capabilities.


**Related Classes/Methods**:

- `output_generators/markdown.py`
- `output_generators/html.py`
- `output_generators/sphinx.py`
- `output_generators/mdx.py`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
