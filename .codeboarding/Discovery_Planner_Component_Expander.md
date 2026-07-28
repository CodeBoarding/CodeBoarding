```mermaid
graph LR
    Discovery_Strategy_Orchestrator["Discovery Strategy Orchestrator"]
    Recursive_Expansion_Engine["Recursive Expansion Engine"]
    Discovery_State_Frontier_Manager["Discovery State & Frontier Manager"]
    Recursive_Expansion_Engine -- "queries expansion policy and depth constraints" --> Discovery_Strategy_Orchestrator
    Recursive_Expansion_Engine -- "persists analysis progress and frontier state" --> Discovery_State_Frontier_Manager
    Discovery_State_Frontier_Manager -- "filters the discovery frontier based on expansion rules" --> Discovery_Strategy_Orchestrator
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Implements the strategy for incremental discovery and depth-control, managing the task queue for deep-dive analysis and breaking down complex systems into manageable sub-components.

### Discovery Strategy Orchestrator
Acts as the decision-making layer that evaluates the current architectural state against depth-control policies and coordinates the planning of analysis tasks.


**Related Classes/Methods**:

- `agents.planner_agent.get_expandable_components`:94-117



**Source Files:**

- [`agents/planner_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/planner_agent.py)
  - `agents.planner_agent.should_expand_component` ([L85-L143](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/planner_agent.py#L85-L143)) - Function
  - `agents.planner_agent.get_expandable_components` ([L146-L181](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/planner_agent.py#L146-L181)) - Function


### Recursive Expansion Engine
The execution core responsible for the drill-down logic, triggering abstraction and static analysis workflows to resolve internal sub-structures.


**Related Classes/Methods**:

- `diagram_analysis.diagram_generator.DiagramGenerator.process_component`:144-147



**Source Files:**

- [`diagram_analysis/diagram_generator.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py)
  - `diagram_analysis.diagram_generator.DiagramGenerator.process_component` ([L548-L551](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L548-L551)) - Method
  - `diagram_analysis.diagram_generator.DiagramGenerator._process_component` ([L642-L667](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L642-L667)) - Method
  - `diagram_analysis.diagram_generator.DiagramGenerator._generate_subcomponents.submit_component` ([L975-L979](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L975-L979)) - Function


### Discovery State & Frontier Manager
Manages the persistence of the discovery process, tracking the analysis frontier to ensure incremental progress and avoid redundant processing.


**Related Classes/Methods**:

- `diagram_analysis.io_utils._AnalysisFileStore._compute_expandable_components`:48-53



**Source Files:**

- [`diagram_analysis/io_utils.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/io_utils.py)
  - `diagram_analysis.io_utils._AnalysisFileStore._compute_expandable_components` ([L49-L54](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/io_utils.py#L49-L54)) - Method
- [`telemetry/events.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtelemetry/events.py)
  - `telemetry.events.track_analysis` ([L160-L222](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtelemetry/events.py#L160-L222)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)