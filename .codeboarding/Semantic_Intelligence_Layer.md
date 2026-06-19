```mermaid
graph LR
    Semantic_Relation_Engine["Semantic Relation Engine"]
    Entity_Clustering_Mapping_Manager["Entity Clustering & Mapping Manager"]
    Abstraction_Workflow_Orchestrator["Abstraction Workflow Orchestrator"]
    Semantic_Relation_Engine -- "Provides raw relational graph and resolved symbols to" --> Entity_Clustering_Mapping_Manager
    Entity_Clustering_Mapping_Manager -- "Supplies organized cluster data and entity maps to" --> Abstraction_Workflow_Orchestrator
    Abstraction_Workflow_Orchestrator -- "Requests symbol resolutions and static relation updates from" --> Semantic_Relation_Engine
    Abstraction_Workflow_Orchestrator -- "calls" --> Entity_Clustering_Mapping_Manager
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Interfaces with static analysis data to answer relational queries about code logic, such as inheritance and dependencies.

### Semantic Relation Engine
Responsible for the 'ground truth' extraction of code semantics, resolving symbol references, and building the foundational relational map.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/tools/base.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py)
  - `agents.tools.base.RepoContext.get_directories` ([L31-L35](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L31-L35)) - Method
- [`agents/tools/get_external_deps.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_external_deps.py)
  - `agents.tools.get_external_deps.ExternalDepsTool._run` ([L24-L47](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_external_deps.py#L24-L47)) - Method


### Entity Clustering & Mapping Manager
Manages the lifecycle and organization of code entities into logical clusters, ensuring uniqueness and creating a hierarchical topology.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/tools/base.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py)
  - `agents.tools.base.RepoContext.get_files` ([L25-L29](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L25-L29)) - Method
- [`agents/tools/read_docs.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_docs.py)
  - `agents.tools.read_docs.ReadDocsTool.cached_files` ([L36-L49](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_docs.py#L36-L49)) - Method


### Abstraction Workflow Orchestrator
The high-level controller that coordinates the multi-step analysis process and interfaces between static data and the Abstraction Agent.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/tools/read_docs.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_docs.py)
  - `agents.tools.read_docs.ReadDocsTool._run` ([L51-L132](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_docs.py#L51-L132)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)