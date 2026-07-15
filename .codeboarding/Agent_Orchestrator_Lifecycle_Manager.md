```mermaid
graph LR
    Agent_Lifecycle_Orchestrator["Agent Lifecycle Orchestrator"]
    Phase_Based_Reasoning_Engine["Phase-Based Reasoning Engine"]
    Prompt_Engineering_Factory["Prompt Engineering Factory"]
    Data_Synthesis_Normalization["Data Synthesis & Normalization"]
    Agent_Lifecycle_Orchestrator -- "manages execution state via inheritance" --> Phase_Based_Reasoning_Engine
    Phase_Based_Reasoning_Engine -- "requests context-aware prompt construction" --> Prompt_Engineering_Factory
    Phase_Based_Reasoning_Engine -- "utilizes data formatting mixins" --> Data_Synthesis_Normalization
    Prompt_Engineering_Factory -- "references project metadata" --> Data_Synthesis_Normalization
    Data_Synthesis_Normalization -- "provides feedback for prompt refinement" --> Prompt_Engineering_Factory
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Manages the state machine and execution flow of specialized agents, handling initialization and coordinating analysis phases.

### Agent Lifecycle Orchestrator
Manages the foundational execution protocol and state transitions for agents, including LLM environment initialization and the main analysis run loop.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/dependency_discovery.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/dependency_discovery.py)
  - `agents.dependency_discovery.discover_dependency_files` ([L103-L159](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/dependency_discovery.py#L103-L159)) - Function


### Phase-Based Reasoning Engine
Implements the logic for each stage of architectural analysis, breaking down system abstraction into discrete steps like API surface identification and relationship analysis.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/tools/base.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py)
  - `agents.tools.base.BaseRepoTool.ignore_manager` ([L80-L81](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L80-L81)) - Method


### Prompt Engineering Factory
Constructs system and user prompts by injecting project-specific context and static analysis results into predefined templates.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/tools/base.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py)
  - `agents.tools.base.BaseRepoTool.is_subsequence` ([L87-L103](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L87-L103)) - Method


### Data Synthesis & Normalization
Refines and validates LLM outputs by cross-referencing with static analysis data, handling entity ID assignment and relationship indexing.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/tools/base.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py)
  - `agents.tools.base.BaseRepoTool.repo_dir` ([L76-L77](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L76-L77)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)