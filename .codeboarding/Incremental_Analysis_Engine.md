```mermaid
graph LR
    Incremental_Orchestrator["Incremental Orchestrator"]
    Change_Impact_Analyzer["Change Impact Analyzer"]
    Analysis_State_Manager["Analysis State Manager"]
    Update_Strategy_Router["Update Strategy Router"]
    Lightweight_Patcher["Lightweight Patcher"]
    Agentic_Re_expansion_Engine["Agentic Re-expansion Engine"]
    File_Component_Mapper["File-Component Mapper"]
    Incremental_Schema["Incremental Schema"]
    Incremental_Orchestrator -- "requests a delta analysis to classify the scope of repository changes" --> Change_Impact_Analyzer
    Change_Impact_Analyzer -- "retrieves the "last known good" state to calculate similarity thresholds" --> Analysis_State_Manager
    Incremental_Orchestrator -- "passes identified "dirty" components to determine the most efficient update path" --> Update_Strategy_Router
    Update_Strategy_Router -- "triggers manifest updates when changes are limited to file paths or metadata" --> Lightweight_Patcher
    Update_Strategy_Router -- "triggers LLM re-analysis when code logic or structural boundaries shift significantly" --> Agentic_Re_expansion_Engine
    Agentic_Re_expansion_Engine -- "persists newly generated AI summaries and updated call graphs to the cache" --> Analysis_State_Manager
    Lightweight_Patcher -- "updates file references in the persistent store without re-running analysis" --> Analysis_State_Manager
    Incremental_Orchestrator -- "resolves modified file paths to their corresponding architectural clusters" --> File_Component_Mapper
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Optimizes analysis performance by managing the caching of static analysis results and orchestrating re-analysis only for changed parts of the codebase, ensuring efficiency and speed.

### Incremental Orchestrator
The central controller that drives the update pipeline, coordinates sub‑components, and performs final integrity validation.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/incremental/updater.py#L55-L464" target="_blank" rel="noopener noreferrer">`IncrementalUpdater`:55-464</a>


### Change Impact Analyzer
Quantifies structural shifts using similarity metrics and classifies changes into categories (e.g., structural vs. internal).


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/incremental/impact_analyzer.py" target="_blank" rel="noopener noreferrer">`ImpactAnalyzer`</a>


### Analysis State Manager
Manages the persistence and retrieval of historical analysis artifacts (call graphs, hierarchies) from the cache.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/analysis_cache.py#L31-L699" target="_blank" rel="noopener noreferrer">`AnalysisCacheManager`:31-699</a>


### Update Strategy Router
Evaluates "dirty" components to decide between a lightweight patch or a full AI‑driven re‑analysis.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/incremental/component_checker.py" target="_blank" rel="noopener noreferrer">`ComponentChecker`</a>


### Lightweight Patcher
Executes fast‑path updates for file renames or moves by patching internal manifests and analysis files.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/incremental/path_patching.py" target="_blank" rel="noopener noreferrer">`PathPatcher`</a>


### Agentic Re-expansion Engine
Orchestrates LLM‑based agents to re‑document components that have undergone significant structural changes.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/incremental/reexpansion.py" target="_blank" rel="noopener noreferrer">`ReexpansionEngine`</a>


### File-Component Mapper
Maintains the alignment between physical source files and the abstract architectural components defined in the cache.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/incremental/file_manager.py" target="_blank" rel="noopener noreferrer">`FileManager`</a>


### Incremental Schema
Defines the shared domain language (ChangeImpact, UpdateAction) used to communicate state across the engine.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/incremental/models.py" target="_blank" rel="noopener noreferrer">`IncrementalModels`</a>




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)