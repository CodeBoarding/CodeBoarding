```mermaid
graph LR
    API_Service["API Service"]
    Job_Management["Job Management"]
    Documentation_Generation["Documentation Generation"]
    CodeBoardingAgent["CodeBoardingAgent"]
    Temporary_Repository_Manager["Temporary Repository Manager"]
    Static_Analysis_Tools["Static Analysis Tools"]
    Configuration_Manager["Configuration Manager"]
    Unclassified["Unclassified"]
    API_Service -- "initiates" --> Job_Management
    Job_Management -- "orchestrates" --> Documentation_Generation
    Documentation_Generation -- "delegates tasks to" --> CodeBoardingAgent
    CodeBoardingAgent -- "utilizes" --> Static_Analysis_Tools
    CodeBoardingAgent -- "accesses" --> Temporary_Repository_Manager
    CodeBoardingAgent -- "retrieves settings from" --> Configuration_Manager
    Static_Analysis_Tools -- "retrieves settings from" --> Configuration_Manager
    Job_Management -- "provides status to" --> API_Service
    CodeBoardingAgent -- "integrates with" --> VS_Code_Environment
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The system's architecture is centered around the `CodeBoardingAgent`, an intelligent orchestrator for documentation generation. User requests are initially handled by the `API Service`, which then passes them to `Job Management` for lifecycle tracking. `Job Management` subsequently triggers `Documentation Generation`, which delegates the core analysis and content creation to the `CodeBoardingAgent`. The `CodeBoardingAgent` performs its tasks by utilizing `Static Analysis Tools` for code understanding, managing temporary repositories via the `Temporary Repository Manager`, and retrieving all necessary operational and VS Code-specific configurations from the `Configuration Manager`. This refined architecture highlights the expanded integration with the VS Code environment, making the `CodeBoardingAgent` a more deeply embedded component within the developer's IDE workflow.

### API Service
Handles all incoming API requests, validates inputs, initiates background jobs, and serves job status and results.


**Related Classes/Methods**:



### Job Management
Manages the persistence and state transitions of documentation generation jobs (e.g., PENDING, RUNNING, COMPLETED, FAILED) using a database.


**Related Classes/Methods**:

- `job_management.JobManager:create_job`:1-10


### Documentation Generation
Orchestrates the overall documentation generation process, delegating the core analysis and content creation to the `CodeBoardingAgent`.


**Related Classes/Methods**:

- `doc_generation.DocGenerator:generate`


### CodeBoardingAgent
An intelligent agent responsible for orchestrating code analysis, information retrieval, and documentation content generation using LLMs and specialized tools. It now includes enhanced integration with VS Code, utilizing `vscode_constants.py` for new commands, configuration options, and interaction patterns within the IDE environment. It interacts with static analysis tools, reads code references, and manages file structures, with its capabilities potentially expanded by new external dependencies.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent.py" target="_blank" rel="noopener noreferrer">`agents.agent.CodeBoardingAgent`</a>


### Temporary Repository Manager
Handles the creation and cleanup of temporary directories used for cloning repositories and storing intermediate analysis results.


**Related Classes/Methods**:

- `temp_repo_manager.TempRepoManager:clone_repository`:1-10


### Static Analysis Tools
Provides enhanced language server functionalities (TypeScript, Pyright) and code analysis tools (tokei, gopls) used by the `CodeBoardingAgent` for in-depth code understanding.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L17-L70" target="_blank" rel="noopener noreferrer">`static_analysis.Analyzer:run_analysis`:17-70</a>


### Configuration Manager
Manages system configuration, including paths to static analysis tools, LLM provider settings, repository roots, and new VS Code-related configurations, primarily through `static_analysis_config.yml` and `.env` files.


**Related Classes/Methods**:

- `config_manager.ConfigManager:load_config`:1-10


### Unclassified
Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)


**Related Classes/Methods**: _None_



### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
