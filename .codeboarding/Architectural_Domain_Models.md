```mermaid
graph LR
    Core_Schema_Entity_Definition["Core Schema & Entity Definition"]
    Hierarchical_Abstraction_Engine["Hierarchical Abstraction Engine"]
    Relation_Dependency_Validator["Relation & Dependency Validator"]
    Hierarchical_Abstraction_Engine -- "populates architectural model" --> Core_Schema_Entity_Definition
    Hierarchical_Abstraction_Engine -- "provides symbol context for edge resolution" --> Relation_Dependency_Validator
    Relation_Dependency_Validator -- "queries symbol table for resolution" --> Hierarchical_Abstraction_Engine
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Contains the core entities of the software architecture domain, representing structural elements and their interconnections.

### Core Schema & Entity Definition
Defines the fundamental data structures and validation logic for architectural entities, providing a unified vocabulary for components, clusters, and relations.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.ComponentRelations.llm_str` ([L606-L609](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L606-L609)) - Method


### Hierarchical Abstraction Engine
Manages the logic for lifting low-level code artifacts into higher-level architectural clusters and mapping physical files to logical architectural views.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.Relation.analysis_dump` ([L380-L386](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L380-L386)) - Method


### Relation & Dependency Validator
Handles the modeling and verification of architectural graph edges, ensuring dependencies are accurately mapped to source code locations.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.Relation.edge_count` ([L377-L378](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L377-L378)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)