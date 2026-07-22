```mermaid
graph LR
    Abstraction_Orchestrator["Abstraction Orchestrator"]
    Structural_Refinement_Indexing["Structural Refinement & Indexing"]
    Architectural_Prompt_Factory["Architectural Prompt Factory"]
    Abstraction_Orchestrator -- "orchestrates data normalization and entity resolution" --> Structural_Refinement_Indexing
    Abstraction_Orchestrator -- "requests context-aware prompts for LLM synthesis" --> Architectural_Prompt_Factory
    Structural_Refinement_Indexing -- "provides structural metadata for prompt weighting" --> Architectural_Prompt_Factory
    Architectural_Prompt_Factory -- "consumes refined models for context compression" --> Structural_Refinement_Indexing
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Orchestration layer that synthesizes deterministic static data into architectural components using AI-augmented logic.

### Abstraction Orchestrator
Manages the high-level lifecycle and state machine of the architectural synthesis process, coordinating the sequential execution of analysis steps.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/source_inspector.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py)
  - `static_analyzer.engine.source_inspector.SourceInspector.__init__` ([L99-L104](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py#L99-L104)) - Method


### Structural Refinement & Indexing
Acts as the deterministic backbone of the engine, performing data normalization, entity resolution, and structural validation to bridge raw static analysis clusters and abstracted components.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/source_inspector.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py)
  - `static_analyzer.engine.source_inspector.SourceInspector._smallest_named_node_ending_at` ([L326-L337](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py#L326-L337)) - Method


### Architectural Prompt Factory
Translates complex structural data and project context into structured prompts for LLM reasoning, encapsulating domain-specific logic for architectural analysis.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/source_inspector.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py)
  - `static_analyzer.engine.source_inspector.SourceInspector._node_size` ([L381-L382](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py#L381-L382)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)