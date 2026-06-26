```mermaid
graph LR
    Repository_Context_Manager["Repository Context Manager"]
    Hierarchical_Structure_Formatter["Hierarchical Structure Formatter"]
    Repository_Context_Manager -- "Provides filtered file paths and metadata to" --> Hierarchical_Structure_Formatter
    Hierarchical_Structure_Formatter -- "Queries for file existence and directory contents" --> Repository_Context_Manager
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Discovers and visualizes the physical file and package organization, handling filtering and hierarchical depth.

### Repository Context Manager
Manages low-level filesystem interaction, identifies project roots, and maintains repository state.


**Related Classes/Methods**:

- `agents.tools.base.RepoContext`:10-54
- `agents.tools.base.RepoContext._perform_walk`:42-54



**Source Files:**

- [`agents/tools/base.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py)
  - `agents.tools.base.RepoContext._ensure_cache` ([L37-L40](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L37-L40)) - Method


### Hierarchical Structure Formatter
Transforms raw file lists into structured, human-readable tree representations with depth control and filtering.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/tools/base.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py)
  - `agents.tools.base.RepoContext._perform_walk` ([L42-L54](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L42-L54)) - Method
  - `agents.tools.base.BaseRepoTool.repo_dir` ([L69-L70](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/tools/base.py#L69-L70)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)