```mermaid
graph LR
    Pipeline_Lifecycle_Controller["Pipeline Lifecycle Controller"]
    State_Persistence_Manager["State & Persistence Manager"]
    Context_Synthesis_Engine["Context Synthesis Engine"]
    Agentic_Reasoning_Orchestrator["Agentic Reasoning Orchestrator"]
    Pipeline_Lifecycle_Controller -- "orchestrates context preparation and finalization" --> Context_Synthesis_Engine
    Pipeline_Lifecycle_Controller -- "dispatches AI reasoning tasks via scope injection" --> Agentic_Reasoning_Orchestrator
    State_Persistence_Manager -- "calls" --> Pipeline_Lifecycle_Controller
    State_Persistence_Manager -- "calls" --> Context_Synthesis_Engine
    Context_Synthesis_Engine -- "triggers post-processing cleanup" --> Pipeline_Lifecycle_Controller
    Context_Synthesis_Engine -- "manages session state and incremental analysis cache" --> State_Persistence_Manager
    Context_Synthesis_Engine -- "calls" --> Agentic_Reasoning_Orchestrator
    Agentic_Reasoning_Orchestrator -- "logs reasoning traces for audit and debugging" --> State_Persistence_Manager
    Agentic_Reasoning_Orchestrator -- "consumes synthesized architectural context" --> Context_Synthesis_Engine
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Manages the high-level execution lifecycle, initializes the RunContext, manages state persistence, and triggers analysis and AI reasoning pipelines.

### Pipeline Lifecycle Controller
Manages the high-level execution flow, coordinating the transition between the static analysis phase and the AI-driven reasoning phase.


**Related Classes/Methods**: _None_


**Source Files:**

- [`caching/cache.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py)
  - `caching.cache.BaseCache.load_most_recent_run` ([L231-L257](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py#L231-L257)) - Method
- [`caching/details_cache.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/details_cache.py)
  - `caching.details_cache.FinalAnalysisCache` ([L18-L34](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/details_cache.py#L18-L34)) - Class
  - `caching.details_cache.ClusterCache` ([L37-L53](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/details_cache.py#L37-L53)) - Class
  - `caching.details_cache.prune_details_caches` ([L56-L58](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/details_cache.py#L56-L58)) - Function


### State & Persistence Manager
Handles the durability and retrieval of analysis sessions, managing the serialization of the RunContext to enable incremental analysis.


**Related Classes/Methods**: _None_


**Source Files:**

- [`codeboarding_workflows/orchestration.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/orchestration.py)
  - `codeboarding_workflows.orchestration.run_analysis_pipeline` ([L25-L48](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/orchestration.py#L25-L48)) - Function
- [`diagram_analysis/run_context.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/run_context.py)
  - `diagram_analysis.run_context._load_existing_run_id` ([L55-L72](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/run_context.py#L55-L72)) - Function


### Context Synthesis Engine
Transforms raw static analysis data into high-level, structured context suitable for LLM consumption by clustering and fixing references.


**Related Classes/Methods**: _None_


**Source Files:**

- [`diagram_analysis/run_context.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/run_context.py)
  - `diagram_analysis.run_context.RunContext` ([L25-L52](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/run_context.py#L25-L52)) - Class
  - `diagram_analysis.run_context.RunContext.resolve` ([L33-L48](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/run_context.py#L33-L48)) - Method
  - `diagram_analysis.run_context.RunContext.finalize` ([L50-L52](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/run_context.py#L50-L52)) - Method
- [`utils.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingutils.py)
  - `utils.generate_run_id` ([L97-L98](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingutils.py#L97-L98)) - Function


### Agentic Reasoning Orchestrator
Executes the multi-step AI reasoning loop using the prepared context to drive the AbstractionAgent for architectural insights.


**Related Classes/Methods**: _None_


**Source Files:**

- [`monitoring/paths.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/paths.py)
  - `monitoring.paths.generate_log_path` ([L25-L27](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/paths.py#L25-L27)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)