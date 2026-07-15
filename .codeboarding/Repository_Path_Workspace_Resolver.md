```mermaid
graph LR
    Workspace_Context_Manager["Workspace Context Manager"]
    Path_Normalization_Reference_Resolver["Path Normalization & Reference Resolver"]
    File_to_Entity_Mapper["File-to-Entity Mapper"]
    Workspace_Context_Manager -- "standardizes project paths for global state" --> Path_Normalization_Reference_Resolver
    Workspace_Context_Manager -- "orchestrates workspace discovery and entity registration" --> File_to_Entity_Mapper
    File_to_Entity_Mapper -- "populates logical model into global context" --> Workspace_Context_Manager
    File_to_Entity_Mapper -- "resolves entity locations and line references" --> Path_Normalization_Reference_Resolver
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Manages translation between Git-tracked paths and local file structures, handling path normalization and .gitignore rules.

### Workspace Context Manager
Manages the global state of the project workspace, including the resolution of project-wide system messages and the initial discovery of file structures.


**Related Classes/Methods**: _None_

### Path Normalization & Reference Resolver
Handles the translation of raw file paths into normalized formats and corrects line-level references within source code to maintain static analysis integrity.


**Related Classes/Methods**: _None_

### File-to-Entity Mapper
Responsible for the logical grouping of file-system artifacts into code entities, populating the internal registry with methods and classes discovered during the workspace scan.


**Related Classes/Methods**: _None_


**Source Files:**

- [`repo_utils/change_detector.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/change_detector.py)
  - `repo_utils.change_detector._overlaps` ([L310-L314](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/change_detector.py#L310-L314)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)