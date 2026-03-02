```mermaid
graph LR
    Incremental_Update_Orchestrator["Incremental Update Orchestrator"]
    Static_Analysis_Cache_Manager["Static Analysis Cache Manager"]
    Structural_Change_Analyzer["Structural Change Analyzer"]
    Agentic_Re_expansion_Engine["Agentic Re‑expansion Engine"]
    File_Path_Patching_Service["File & Path Patching Service"]
    Incremental_Persistence_Layer["Incremental Persistence Layer"]
    Integrity_Validator["Integrity Validator"]
    Incremental_Update_Orchestrator -- "triggers analysis to define UpdateAction" --> Structural_Change_Analyzer
    Incremental_Update_Orchestrator -- "invokes engine when changes exceed SMALL threshold" --> Agentic_Re_expansion_Engine
    Agentic_Re_expansion_Engine -- "streams updated component fragments to persistence layer" --> Incremental_Persistence_Layer
    File_Path_Patching_Service -- "directly modifies cache's file‑to‑component mappings" --> Static_Analysis_Cache_Manager
    Integrity_Validator -- "inspects final state of cache before update completion" --> Static_Analysis_Cache_Manager
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Optimizes analysis performance by managing the caching of static analysis results and orchestrating re-analysis only for changed parts of the codebase, ensuring efficiency and speed.

### Incremental Update Orchestrator
Central controller (IncrementalUpdater) that manages the update lifecycle, deciding between patching, re‑expansion, or full re‑analysis.


**Related Classes/Methods**:

- `diagram_analysis.incremental.updater.IncrementalUpdater`


### Static Analysis Cache Manager
Manages the AnalysisCache, acting as the source‑of‑truth for call graphs, class hierarchies, and previous diagnostics.


**Related Classes/Methods**:

- `diagram_analysis.incremental.updater.AnalysisCache`


### Structural Change Analyzer
Evaluates differences (ClusterChangeAnalyzer) between iterations to classify changes (SMALL/MEDIUM/BIG) and map file diffs to architectural impacts.


**Related Classes/Methods**:

- `diagram_analysis.incremental.updater.ClusterChangeAnalyzer`


### Agentic Re‑expansion Engine
LLM‑driven component that re‑synthesizes descriptions and relationships for "dirty" components using meta‑agents.


**Related Classes/Methods**:

- `diagram_analysis.incremental.updater.AgenticReexpansionEngine`


### File & Path Patching Service
Handles low‑level state updates such as file renames and deletions that do not require LLM intervention.


**Related Classes/Methods**:

- `diagram_analysis.incremental.updater.FilePathPatchingService`


### Incremental Persistence Layer
Provides thread‑safe I/O (_AnalysisFileStore) for atomic loading and saving of root and sub‑analysis fragments.


**Related Classes/Methods**:

- `diagram_analysis.incremental.updater._AnalysisFileStore`


### Integrity Validator
Performs post‑update consistency checks on the merged call graph and component mappings to prevent state corruption.


**Related Classes/Methods**:

- `diagram_analysis.incremental.updater.IntegrityValidator`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)