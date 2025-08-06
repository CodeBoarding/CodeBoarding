```mermaid
graph LR
    api_layer["api_layer"]
    orchestration_workflow["orchestration_workflow"]
    static_analysis_module["static_analysis_module"]
    ai_analysis_engine["ai_analysis_engine"]
    data_persistence["data_persistence"]
    output_generation["output_generation"]
    integrations["integrations"]
    configuration["configuration"]
    api_layer -- "initiates analysis requests" --> orchestration_workflow
    orchestration_workflow -- "returns processed results" --> api_layer
    orchestration_workflow -- "invokes" --> static_analysis_module
    orchestration_workflow -- "submits static analysis results to" --> ai_analysis_engine
    orchestration_workflow -- "stores results in" --> data_persistence
    data_persistence -- "provides cached data to" --> orchestration_workflow
    orchestration_workflow -- "provides processed data to" --> output_generation
    integrations -- "provides raw source code to" --> orchestration_workflow
    configuration -- "provides settings to" --> api_layer
    configuration -- "provides settings to" --> orchestration_workflow
    configuration -- "provides settings to" --> static_analysis_module
    configuration -- "provides settings to" --> ai_analysis_engine
    configuration -- "provides settings to" --> data_persistence
    configuration -- "provides settings to" --> output_generation
    configuration -- "provides settings to" --> integrations
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/GeneratedOnBoardings)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/demo)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The `Orchestration & Workflow` component is central to the system, coordinating the entire analysis pipeline. Its interactions with other components are crucial for the system's functionality.

### api_layer
The primary interface for external communication, responsible for receiving analysis requests via HTTP and returning the final processed results. It acts as the system's public-facing entry point.


**Related Classes/Methods**:

- `local_app` (1:1)
- `github_action` (1:1)


### orchestration_workflow
The central coordinator that manages the end-to-end analysis pipeline. It initiates static analysis, triggers the AI engine, coordinates with the persistence layer for caching and differential checks, and sends the final, validated results to the output generator.


**Related Classes/Methods**:

- `local_app` (1:1)
- `github_action` (1:1)


### static_analysis_module
Responsible for parsing source code, building abstract syntax trees (ASTs), control flow graphs (CFGs), and extracting raw code metadata. It provides the foundational data for subsequent analysis steps.


**Related Classes/Methods**:

- `astroid` (1:1)
- `python-call-graph` (1:1)
- `networkx` (1:1)


### ai_analysis_engine
Encapsulates all interactions with Large Language Models (LLMs), including prompt engineering, multi-provider support (OpenAI, Anthropic, Google Gemini, AWS Bedrock), and processing LLM responses to derive high-level insights and recommendations.


**Related Classes/Methods**:

- `LangChain` (1:1)
- `LangGraph` (1:1)
- `OpenAI` (1:1)
- `Anthropic` (1:1)
- `Google Gemini` (1:1)
- `AWS Bedrock` (1:1)


### data_persistence
Manages data storage and retrieval for analysis results, intermediate data, and caching mechanisms. It abstracts the underlying database (e.g., DuckDB via SQLAlchemy) to provide a consistent data access layer.


**Related Classes/Methods**:

- `SQLAlchemy` (1:1)
- `DuckDB` (1:1)


### output_generation
Transforms processed analysis data into various user-consumable output formats, particularly interactive diagrams (e.g., Mermaid.js, pygraphviz, pydot) and other visualizations.


**Related Classes/Methods**:

- `pygraphviz` (1:1)
- `pydot` (1:1)
- `Mermaid.js` (1:1)


### integrations
Handles specific logic related to external services, such as cloning repositories from GitHub (using GitPython, Dulwich) and potential integrations with other platforms (VS Code, MCP Server).


**Related Classes/Methods**:

- `GitPython` (1:1)
- `Dulwich` (1:1)


### configuration
Centralizes application settings, environment variable loading, and credential management, ensuring consistent and secure configuration across all components of the system.


**Related Classes/Methods**:

- `config_module` (1:1)




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
