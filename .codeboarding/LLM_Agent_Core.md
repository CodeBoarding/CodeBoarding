```mermaid
graph LR
    Agent_Orchestration_Engine["Agent Orchestration Engine"]
    Specialized_Semantic_Agents["Specialized Semantic Agents"]
    Prompt_Management_System["Prompt Management System"]
    Semantic_Validation_Engine["Semantic Validation Engine"]
    LLM_Infrastructure_Config["LLM Infrastructure & Config"]
    Static_Analysis_Cluster_Utils["Static Analysis & Cluster Utils"]
    Semantic_Data_Models["Semantic Data Models"]
    Dependency_Discovery_Service["Dependency Discovery Service"]
    Agent_Orchestration_Engine -- "delegates granular analysis tasks" --> Specialized_Semantic_Agents
    Specialized_Semantic_Agents -- "retrieves task‑specific templates" --> Prompt_Management_System
    Specialized_Semantic_Agents -- "fetches code snippets and subgraph data for context" --> Static_Analysis_Cluster_Utils
    Semantic_Validation_Engine -- "validates LLM‑derived components against CFG facts" --> Static_Analysis_Cluster_Utils
    LLM_Infrastructure_Config -- "supplies initialized model clients for execution" --> Specialized_Semantic_Agents
    Specialized_Semantic_Agents -- "serializes LLM responses into structured objects" --> Semantic_Data_Models
    Dependency_Discovery_Service -- "provides high‑level project metadata to seed the analysis" --> Agent_Orchestration_Engine
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The intelligent core responsible for driving the code analysis and documentation generation using large language models. It orchestrates agent workflows, manages interactions with various tools, and structures the analysis insights.

### Agent Orchestration Engine
Coordinates the analysis workflow, manages agent lifecycles, and ensures full coverage of CFG clusters.


**Related Classes/Methods**:

- `agents.code_boarding.CodeBoardingAgent`


### Specialized Semantic Agents
Task‑specific agents (Abstraction, Meta, Details) that perform targeted analysis of code components.


**Related Classes/Methods**:

- `agents.abstraction.AbstractionAgent`
- `agents.meta.MetaAgent`
- `agents.details.DetailsAgent`


### Prompt Management System
Decouples prompt engineering from logic, providing provider‑specific templates (OpenAI, Gemini, etc.).


**Related Classes/Methods**:

- `prompts.prompt_generator.PromptGenerator`
- `prompts.prompt_factory.PromptFactory`:49-99


### Semantic Validation Engine
Cross‑references LLM interpretations with static analysis facts to ensure no clusters are missed or hallucinated.


**Related Classes/Methods**:

- `validation.ValidationContext`:14-27


### LLM Infrastructure & Config
Manages provider configurations, API keys, and model initialization (Ollama, Anthropic, etc.).


**Related Classes/Methods**:

- `config.llm_config.LLMConfig`
- `config.initialize_llms`:319-322


### Static Analysis & Cluster Utils
Provides utility methods for agents to map clusters to file sets and extract relevant code subgraphs.


**Related Classes/Methods**:

- `utils.cluster_utils`


### Semantic Data Models
Defines the structured Pydantic schemas for LLM communication and final documentation output.


**Related Classes/Methods**:

- `models.analysis_insights.AnalysisInsights`
- `models.component.Component`
- `models.relation.Relation`


### Dependency Discovery Service
Scans for manifest files (e.g., package.json) to provide ecosystem context to the agents.


**Related Classes/Methods**:

- `discovery.discover_dependency_files`:103-159




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)