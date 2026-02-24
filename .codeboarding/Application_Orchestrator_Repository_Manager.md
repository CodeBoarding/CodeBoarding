```mermaid
graph LR
    Analysis_Orchestrator["Analysis Orchestrator"]
    Repository_Manager["Repository Manager"]
    Change_Detector["Change Detector"]
    Tooling_Registry["Tooling Registry"]
    Analysis_Schema_Engine["Analysis Schema Engine"]
    Persistence_Controller["Persistence Controller"]
    Configuration_Provider["Configuration Provider"]
    Integration_Diagnostics["Integration & Diagnostics"]
    Analysis_Orchestrator -- "triggers" --> Repository_Manager
    Repository_Manager -- "utilizes" --> Change_Detector
    Analysis_Orchestrator -- "requests" --> Tooling_Registry
    Integration_Diagnostics -- "verifies" --> Tooling_Registry
    Analysis_Orchestrator -- "feeds" --> Analysis_Schema_Engine
    Analysis_Orchestrator -- "updates" --> Persistence_Controller
    Configuration_Provider -- "supplies" --> Analysis_Orchestrator
    Change_Detector -- "stores" --> Persistence_Controller
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Manages the overall application lifecycle, including project initialization, repository operations (cloning, updating), change detection, and orchestrating the analysis workflow. It also handles the initial setup and environment configuration for the analysis tools.

### Analysis Orchestrator
Coordinates the end-to-end workflow, from initialization to final schema generation.


**Related Classes/Methods**:

- `analysis.orchestrator`


### Repository Manager
Handles Git operations (clone, pull) and manages the local workspace.


**Related Classes/Methods**:

- `repo_utils.repository_manager`


### Change Detector
Calculates file hashes and diffs to enable incremental analysis updates.


**Related Classes/Methods**:

- `repo_utils.change_detector`


### Tooling Registry
Provisions and resolves paths for static analysis binaries (e.g., Pyright, JDTLS).


**Related Classes/Methods**:

- `tools.registry`


### Analysis Schema Engine
Transforms raw analysis data into the UnifiedAnalysisJson format.


**Related Classes/Methods**:

- `schema.engine`


### Persistence Controller
Manages long-term storage of job states and metadata using DuckDB.


**Related Classes/Methods**:

- `persistence.db`


### Configuration Provider
Resolves and validates LLM provider settings and environment variables.


**Related Classes/Methods**:

- `config.provider`


### Integration & Diagnostics
Performs system health checks and ensures VS Code compatibility.


**Related Classes/Methods**:

- `diagnostics.health`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)