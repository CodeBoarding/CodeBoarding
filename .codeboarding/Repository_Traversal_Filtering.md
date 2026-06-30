```mermaid
graph LR
    Traversal_Orchestrator_Tech_Identifier["Traversal Orchestrator & Tech Identifier"]
    Exclusion_Filter_Manager["Exclusion & Filter Manager"]
    Ecosystem_Dependency_Discovery["Ecosystem & Dependency Discovery"]
    Incremental_State_Tracker["Incremental State Tracker"]
    LSP_Workspace_Mapper["LSP Workspace Mapper"]
    Traversal_Orchestrator_Tech_Identifier -- "consults" --> Exclusion_Filter_Manager
    Traversal_Orchestrator_Tech_Identifier -- "initializes" --> LSP_Workspace_Mapper
    Exclusion_Filter_Manager -- "provides filtering logic to" --> Ecosystem_Dependency_Discovery
    Ecosystem_Dependency_Discovery -- "feeds manifest files to" --> Incremental_State_Tracker
    Traversal_Orchestrator_Tech_Identifier -- "provides file list to" --> Incremental_State_Tracker
    Exclusion_Filter_Manager -- "calls" --> Traversal_Orchestrator_Tech_Identifier
    Ecosystem_Dependency_Discovery -- "calls" --> Traversal_Orchestrator_Tech_Identifier
    Ecosystem_Dependency_Discovery -- "calls" --> Exclusion_Filter_Manager
    Incremental_State_Tracker -- "calls" --> Exclusion_Filter_Manager
    Incremental_State_Tracker -- "calls" --> Ecosystem_Dependency_Discovery
    Incremental_State_Tracker -- "calls" --> LSP_Workspace_Mapper
    LSP_Workspace_Mapper -- "calls" --> Exclusion_Filter_Manager
    LSP_Workspace_Mapper -- "calls" --> Incremental_State_Tracker
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Manages the physical inspection of the file system, implements ignore logic, and generates repository state hashes for incremental analysis.

### Traversal Orchestrator & Tech Identifier
The central engine for physical repository inspection. It walks the file system to identify programming languages, maps file extensions to profiles, and categorizes the repository's technical stack using tools like Tokei.


**Related Classes/Methods**:

- `static_analyzer.scanner.ProjectScanner`:64-179
- `static_analyzer.scanner.ProjectScanner.scan`:69-161



**Source Files:**

- [`agents/dependency_discovery.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/dependency_discovery.py)
  - `agents.dependency_discovery.DiscoveredDependencyFile` ([L98-L100](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/dependency_discovery.py#L98-L100)) - Class
  - `agents.dependency_discovery.discover_dependency_files` ([L103-L159](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/dependency_discovery.py#L103-L159)) - Function
- [`repo_utils/ignore.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py)
  - `repo_utils.ignore.RepoIgnoreManager.strip_ignored` ([L257-L287](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py#L257-L287)) - Method
- [`static_analyzer/engine/language_adapter.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/language_adapter.py)
  - `static_analyzer.engine.language_adapter.LanguageAdapter._walk` ([L252-L265](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/language_adapter.py#L252-L265)) - Method
- [`static_analyzer/scanner.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py)
  - `static_analyzer.scanner.ProjectScanner` ([L64-L179](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L64-L179)) - Class
  - `static_analyzer.scanner.ProjectScanner.__init__` ([L65-L67](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L65-L67)) - Method


### Exclusion & Filter Manager
Acts as the gatekeeper for all traversal operations. It aggregates exclusion rules from .gitignore, .codeboardingignore, and internal defaults to provide a unified filtering API that prevents noise and sensitive data from entering the analysis pipeline.


**Related Classes/Methods**:

- `repo_utils.ignore.RepoIgnoreManager`:164-329
- `repo_utils.ignore.RepoIgnoreManager.should_ignore`:223-251
- `repo_utils.ignore.RepoIgnoreManager.filter_paths`:253-255



**Source Files:**

- [`agents/dependency_discovery.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/dependency_discovery.py)
  - `agents.dependency_discovery.discover_dependency_files._walk` ([L127-L150](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/dependency_discovery.py#L127-L150)) - Function
- [`repo_utils/ignore.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py)
  - `repo_utils.ignore.RepoIgnoreManager.should_ignore` ([L223-L251](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py#L223-L251)) - Method
  - `repo_utils.ignore.RepoIgnoreManager.filter_paths` ([L253-L255](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py#L253-L255)) - Method


### Ecosystem & Dependency Discovery
Specifically targets "brain files" (manifests like package.json or pyproject.toml) to map the project's dependency graph and ecosystem boundaries.


**Related Classes/Methods**:

- `agents.dependency_discovery.discover_dependency_files`:103-159



**Source Files:**

- [`caching/meta_cache.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/meta_cache.py)
  - `caching.meta_cache.MetaCache.discover_metadata_files` ([L57-L69](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/meta_cache.py#L57-L69)) - Method
- [`static_analyzer/scanner.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py)
  - `static_analyzer.scanner._format_command` ([L16-L21](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L16-L21)) - Function
  - `static_analyzer.scanner.ProjectScanner.scan` ([L69-L161](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L69-L161)) - Method
  - `static_analyzer.scanner.ProjectScanner._extract_suffixes` ([L164-L179](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L164-L179)) - Method


### Incremental State Tracker
Manages repository "snapshots" by hashing file contents and directory structures. It enables incremental analysis by identifying exactly which components have changed since the last successful scan.


**Related Classes/Methods**: _None_


**Source Files:**

- [`repo_utils/__init__.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/__init__.py)
  - `repo_utils.__init__.get_repo_state_hash` ([L193-L223](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/__init__.py#L193-L223)) - Function
- [`static_analyzer/scanner.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py)
  - `static_analyzer.scanner._format_stderr` ([L24-L30](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L24-L30)) - Function
  - `static_analyzer.scanner._tokei_failure_message` ([L33-L61](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py#L33-L61)) - Function
- [`static_analyzer/typescript_config_scanner.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/typescript_config_scanner.py)
  - `static_analyzer.typescript_config_scanner.TypeScriptConfigScanner._fallback_walk` ([L155-L175](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/typescript_config_scanner.py#L155-L175)) - Method


### LSP Workspace Mapper
Translates the physical file structure into logical Language Server Protocol (LSP) workspaces. It identifies root directories for different languages and configures the environment for deep static analysis.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/typescript_config_scanner.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/typescript_config_scanner.py)
  - `static_analyzer.typescript_config_scanner.TypeScriptConfigScanner._resolve_project_files` ([L105-L127](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/typescript_config_scanner.py#L105-L127)) - Method
  - `static_analyzer.typescript_config_scanner.TypeScriptConfigScanner._showconfig` ([L129-L153](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/typescript_config_scanner.py#L129-L153)) - Method
  - `static_analyzer.typescript_config_scanner._is_ancestor` ([L207-L212](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/typescript_config_scanner.py#L207-L212)) - Function
- [`tool_registry/paths.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/paths.py)
  - `tool_registry.paths.is_wsl` ([L34-L48](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/paths.py#L34-L48)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)