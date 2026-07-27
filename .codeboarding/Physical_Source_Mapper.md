```mermaid
graph LR
    Source_Context_Provider["Source Context Provider"]
    Coordinate_Reconciler["Coordinate Reconciler"]
    Physical_Entity_Registry["Physical Entity Registry"]
    Source_Context_Provider -- "provides raw content for sequence matching and repair" --> Coordinate_Reconciler
    Source_Context_Provider -- "resolves logical entity names to file system paths" --> Physical_Entity_Registry
    Coordinate_Reconciler -- "validates and updates entity physical mappings" --> Physical_Entity_Registry
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Handles the low-level mapping between the file system's text and the logical AST nodes, identifying code entity boundaries.

### Source Context Provider
Handles the retrieval and formatting of raw source code to provide context for the agentic workflow, bridging the file system and the prompt engine.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/hierarchy_builder.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/hierarchy_builder.py)
  - `static_analyzer.engine.hierarchy_builder.HierarchyBuilder.__init__` ([L22-L32](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/hierarchy_builder.py#L22-L32)) - Method


### Coordinate Reconciler
Specializes in Range Resolution to fix and validate line numbers and file offsets, reconciling static analysis data with the current state of the source code.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/source_inspector.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py)
  - `static_analyzer.engine.source_inspector.SourceInspector._node_contains_point` ([L357-L366](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py#L357-L366)) - Method


### Physical Entity Registry
Manages the mapping of logical code entities to their physical containers, ensuring unique keys and structural metadata for analysis insights.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/source_inspector.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py)
  - `static_analyzer.engine.source_inspector.SourceInspector._read_file_bytes` ([L189-L196](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/source_inspector.py#L189-L196)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)