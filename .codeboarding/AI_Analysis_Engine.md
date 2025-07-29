```mermaid
graph LR
    CodeBoardingAgent["CodeBoardingAgent"]
    PlannerAgent["PlannerAgent"]
    AbstractionAgent["AbstractionAgent"]
    DiffAnalyzingAgent["DiffAnalyzingAgent"]
    PlannerAgent -- "inherits from" --> CodeBoardingAgent
    AbstractionAgent -- "inherits from" --> CodeBoardingAgent
    DiffAnalyzingAgent -- "inherits from" --> CodeBoardingAgent
    PlannerAgent -- "orchestrates" --> AbstractionAgent
    PlannerAgent -- "uses" --> DiffAnalyzingAgent
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/GeneratedOnBoardings)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/demo)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose.

### CodeBoardingAgent
Acts as the abstract base class for all specialized agents. It implements the Template Method pattern by providing a standardized framework that encapsulates common concerns like AI model interaction, environment variable setup, and access to shared tools. This ensures consistency and reusability across the agent system.


**Related Classes/Methods**:

- `agents/agent.py`


### PlannerAgent
Serves as the primary entry point and controller for an analysis task. It receives a high-level goal, breaks it down into a concrete, multi-step execution plan, and orchestrates the workflow by delegating specific tasks to the appropriate worker agents (like the AbstractionAgent).


**Related Classes/Methods**:

- `agents/planner_agent.py`


### AbstractionAgent
The main worker agent responsible for executing the core architectural analysis. Following the plan from the PlannerAgent, it interacts directly with source code and Control Flow Graph (CFG) data to identify patterns, define component responsibilities, and generate the raw insights for the codebase model.


**Related Classes/Methods**:

- `agents/abstraction_agent.py`


### DiffAnalyzingAgent
A specialized agent that enables efficient incremental updates. It analyzes Git diffs to identify the precise scope of code changes, allowing the system to perform targeted re-analysis instead of processing the entire codebase again. This implements a form of Change Data Capture (CDC).


**Related Classes/Methods**:

- `agents/diff_analyzer.py`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)