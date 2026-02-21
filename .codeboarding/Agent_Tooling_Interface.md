```mermaid
graph LR
    Tool_Registry_Orchestrator["Tool Registry & Orchestrator"]
    Static_Analysis_Engine["Static Analysis Engine"]
    File_System_Source_Access["File System & Source Access"]
    Dependency_Ecosystem_Mapper["Dependency & Ecosystem Mapper"]
    Documentation_Git_Context_Provider["Documentation & Git Context Provider"]
    Infrastructure_State_Manager["Infrastructure & State Manager"]
    Tool_Registry_Orchestrator -- "dispatches complex code queries to specialized extractors" --> Static_Analysis_Engine
    Tool_Registry_Orchestrator -- "requests raw file data or directory listings" --> File_System_Source_Access
    Static_Analysis_Engine -- "queries the metadata store and cache" --> Infrastructure_State_Manager
    Dependency_Ecosystem_Mapper -- "utilizes class and package info to build high-level dependency graphs" --> Static_Analysis_Engine
    File_System_Source_Access -- "consults ignore-pattern enforcement" --> Infrastructure_State_Manager
    Documentation_Git_Context_Provider -- "uses file discovery to locate relevant files" --> File_System_Source_Access
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Provides a set of specialized tools that allow the LLM Agent Core to interact with the codebase, query static analysis results, and perform specific actions within the project context.

### Tool Registry & Orchestrator
Acts as the central factory and dispatcher; it aggregates all specialized tools into a unified interface, managing tool registration and lifecycle for the LLM Agent.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/__init__.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.ToolRegistry`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/__init__.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.AgentToolFactory`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/__init__.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.ToolDispatcher`</a>


### Static Analysis Engine
Extracts high-level code intelligence, including Control Flow Graphs (CFG), class hierarchies, and method signatures to provide structural understanding.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_method_invocations.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.GetCFGTool`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_method_invocations.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.CodeStructureTool`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_method_invocations.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.analysis.ClassHierarchyAnalyzer`</a>


### File System & Source Access
Provides low-level access to the project's physical structure, enabling directory exploration and granular file content retrieval.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_file.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.ReadFileTool`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_file.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.DirectoryTreeGenerator`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_file.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.FileSystemExplorer`</a>


### Dependency & Ecosystem Mapper
Maps internal package relationships and identifies external library dependencies to define the project's architectural boundaries.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_external_deps.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.DependencyMapperTool`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_external_deps.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.analysis.PackageRelationshipAnalyzer`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/get_external_deps.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.analysis.ExternalLibraryScanner`</a>


### Documentation & Git Context Provider
Retrieves non-code context, such as Markdown documentation and Git diffs, to provide historical grounding and design intent.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_docs.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.MarkdownDocTool`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_docs.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.GitDiffTool`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/read_docs.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.tools.ContextRetriever`</a>


### Infrastructure & State Manager
Manages repository-level state, including the DuckDB metadata store, caching mechanisms, and global ignore-pattern enforcement.


**Related Classes/Methods**: _None_



### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)