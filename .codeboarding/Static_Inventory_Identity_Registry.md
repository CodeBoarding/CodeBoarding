```mermaid
graph LR
    Entity_Identity_Key_Manager["Entity Identity & Key Manager"]
    Physical_Source_Resolver["Physical Source Resolver"]
    Static_Inventory_Aggregator["Static Inventory Aggregator"]
    Physical_Source_Resolver -- "resolves canonical names for source mapping" --> Entity_Identity_Key_Manager
    Physical_Source_Resolver -- "queries structured inventory for reference lookups" --> Static_Inventory_Aggregator
    Static_Inventory_Aggregator -- "registers discovered symbols for identity management" --> Entity_Identity_Key_Manager
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Maintains the deterministic ground truth of the codebase, providing identity systems and file-level metadata for validation.

### Entity Identity & Key Manager
Responsible for maintaining the uniqueness and integrity of architectural entities by assigning and validating unique keys and component IDs.


**Related Classes/Methods**: _None_

### Physical Source Resolver
Maps abstract architectural components to concrete source code implementations, validating file paths and correcting line numbers for accurate references.


**Related Classes/Methods**: _None_

### Static Inventory Aggregator
Compiles raw static analysis data into structured inventories and API surface descriptions for architectural reasoning.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.ScopeRelations.llm_str` ([L813-L816](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L813-L816)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)