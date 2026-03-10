```mermaid
graph LR
    Analysis_Planner_Agent["Analysis Planner Agent"]
    Orchestration_Engine -- "receives from" --> Analysis_Planner_Agent
    Analysis_Planner_Agent -- "sends to" --> Static_Analysis_Engine
    Analysis_Planner_Agent -- "sends to" --> AI_Interpretation_Layer
    Analysis_Planner_Agent -- "sends to" --> Output_Generation_Engine
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Strategically determines the optimal plan and sequence for the analysis workflow, deciding which code entities need deeper investigation based on current insights and goals.

### Analysis Planner Agent
Core component of the Analysis Planning Engine that strategically determines the optimal plan and sequence for the analysis workflow, deciding which code entities need deeper investigation based on current insights and goals. It orchestrates tasks, interacts with other engines, and monitors progress.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/planner_agent.py" target="_blank" rel="noopener noreferrer">`agents.planner_agent.AnalysisPlannerAgent`</a>




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)