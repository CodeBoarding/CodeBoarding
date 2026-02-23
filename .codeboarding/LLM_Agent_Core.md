```mermaid
graph LR
    Agent_Orchestrator["Agent Orchestrator"]
    Contextualizer["Contextualizer"]
    Architectural_Abstractor["Architectural Abstractor"]
    Strategic_Planner["Strategic Planner"]
    Detail_Analyzer["Detail Analyzer"]
    Grounding_Engine["Grounding Engine"]
    Prompt_Factory["Prompt Factory"]
    Schema_Registry["Schema Registry"]
    Agent_Orchestrator -- "coordinates" --> Contextualizer
    Agent_Orchestrator -- "triggers" --> Architectural_Abstractor
    Architectural_Abstractor -- "validates clusters via" --> Grounding_Engine
    Agent_Orchestrator -- "consults" --> Strategic_Planner
    Strategic_Planner -- "directs analysis of" --> Detail_Analyzer
    Detail_Analyzer -- "validates sub‑graphs via" --> Grounding_Engine
    Detail_Analyzer -- "uses" --> Prompt_Factory
    Architectural_Abstractor -- "uses" --> Prompt_Factory
    Agent_Orchestrator -- "enforces contracts via" --> Schema_Registry
    Detail_Analyzer -- "adheres to" --> Schema_Registry
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The intelligent core responsible for driving the code analysis and documentation generation using large language models. It orchestrates agent workflows, manages interactions with various tools, and structures the analysis insights.

### Agent Orchestrator
The central controller managing the pipeline lifecycle, state transitions, and handoffs between specialized agents. It maintains the global state of the analysis job.


**Related Classes/Methods**:

- `repos.codeboarding.agent.CodeBoardingAgent`


### Contextualizer
Analyzes project‑wide metadata (tech stack, domain, READMEs) to provide the grounding context for all subsequent LLM reasoning.


**Related Classes/Methods**:

- `repos.codeboarding.agent.MetaAgent`


### Architectural Abstractor
Performs high‑level clustering of files and modules into logical architectural components based on naming conventions and structural proximity.


**Related Classes/Methods**:

- `repos.codeboarding.agent.AbstractionAgent`


### Strategic Planner
Evaluates the complexity of the initial abstraction and determines which specific components require granular "drill‑down" analysis.


**Related Classes/Methods**:

- `repos.codeboarding.agent.PlannerAgent`


### Detail Analyzer
Conducts deep‑dive inspections of specific components to extract internal logic, sub‑graph relationships, and fine‑grained dependencies.


**Related Classes/Methods**:

- `repos.codeboarding.agent.DetailsAgent`


### Grounding Engine
Cross‑references LLM‑proposed mappings with deterministic CFG data to ensure structural validity and prevent hallucinations.


**Related Classes/Methods**:

- `repos.codeboarding.agent.ValidationContext`


### Prompt Factory
A decoupled interface for generating provider‑specific prompts (Gemini, OpenAI, etc.) and managing templates to ensure consistent agent behavior.


**Related Classes/Methods**:

- `repos.codeboarding.prompts.PromptFactory`
- `repos.codeboarding.prompts.PromptGenerator`


### Schema Registry
Defines the Pydantic models and structured contracts used for inter‑agent communication and final output serialization.


**Related Classes/Methods**:

- `repos.codeboarding.models.AnalysisInsights`
- `repos.codeboarding.models.AgentResponses`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)