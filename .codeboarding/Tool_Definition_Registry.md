```mermaid
graph LR
    Tool_Metadata_Registry_Core["Tool Metadata & Registry Core"]
    Source_Resolution_Platform_Mapping["Source Resolution & Platform Mapping"]
    Provisioning_Path_Orchestration["Provisioning & Path Orchestration"]
    Source_Resolution_Platform_Mapping -- "provides concrete source implementations to" --> Tool_Metadata_Registry_Core
    Provisioning_Path_Orchestration -- "queries tool definitions for installation" --> Tool_Metadata_Registry_Core
    Provisioning_Path_Orchestration -- "executes downloads from resolved origins" --> Source_Resolution_Platform_Mapping
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Acts as the Source of Truth for tool dependencies, defining schemas for origins and classifications of LSPs, Runtimes, and Utilities.

### Tool Metadata & Registry Core
Acts as the central source of truth and manifest for tool definitions, taxonomy, and configuration schemas.


**Related Classes/Methods**:

- `tool_registry.registry.ConfigSection`:65-69



**Source Files:**

- [`tool_registry/registry.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py)
  - `tool_registry.registry.ConfigSection` ([L65-L69](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py#L65-L69)) - Class
  - `tool_registry.registry.GitHubToolSource` ([L80-L101](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py#L80-L101)) - Class


### Source Resolution & Platform Mapping
Handles logic for locating, validating, and mapping tool binaries across different hosting environments and architectures.


**Related Classes/Methods**: _None_


**Source Files:**

- [`tool_registry/registry.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py)
  - `tool_registry.registry.ToolKind` ([L54-L62](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py#L54-L62)) - Class
  - `tool_registry.registry.PackageManagerToolSource` ([L113-L121](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py#L113-L121)) - Class
  - `tool_registry.registry.ToolDependency` ([L125-L150](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py#L125-L150)) - Class


### Provisioning & Path Orchestration
Translates registry entries into local assets, managing filesystem paths, installation routines, and environment integrity via fingerprinting.


**Related Classes/Methods**: _None_


**Source Files:**

- [`tool_registry/registry.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py)
  - `tool_registry.registry.ToolSource` ([L73-L76](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py#L73-L76)) - Class
  - `tool_registry.registry.UpstreamToolSource` ([L105-L109](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingtool_registry/registry.py#L105-L109)) - Class




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)