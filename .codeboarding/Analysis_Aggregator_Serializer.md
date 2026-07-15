```mermaid
graph LR
    Analysis_Orchestration_Engine["Analysis Orchestration Engine"]
    Entity_Synthesis_Identity_Resolver["Entity Synthesis & Identity Resolver"]
    Cluster_Aggregator["Cluster Aggregator"]
    Report_Serializer_Insight_Formatter["Report Serializer & Insight Formatter"]
    Analysis_Orchestration_Engine -- "delegates entity unification and mapping" --> Entity_Synthesis_Identity_Resolver
    Analysis_Orchestration_Engine -- "orchestrates architectural scoping and clustering" --> Cluster_Aggregator
    Analysis_Orchestration_Engine -- "triggers final model serialization" --> Report_Serializer_Insight_Formatter
    Entity_Synthesis_Identity_Resolver -- "references cluster hierarchy for identity resolution" --> Cluster_Aggregator
    Cluster_Aggregator -- "provides structural metadata for formatting" --> Report_Serializer_Insight_Formatter
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Responsible for aggregating individual architectural entities into a cohesive analysis report and handling serialization.

### Analysis Orchestration Engine
Manages the high-level execution flow and coordinates the sequence of analysis steps to ensure context is maintained throughout the abstraction process.


**Related Classes/Methods**: _None_

### Entity Synthesis & Identity Resolver
Unifies architectural entities by resolving naming collisions, ensuring unique identity keys, and mapping static code references to synthesized component definitions.


**Related Classes/Methods**: _None_

### Cluster Aggregator
Handles the grouping and hierarchical organization of code entities into logical clusters representing the system's macro-architecture.


**Related Classes/Methods**: _None_

### Report Serializer & Insight Formatter
Converts the internal synthesized model into structured output and formats LLM insights for rendering engines.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.Relation.llm_str` ([L324-L325](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L324-L325)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)