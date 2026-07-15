```mermaid
graph LR
    LSP_Protocol_Orchestrator["LSP Protocol Orchestrator"]
    Semantic_Symbol_Resolver["Semantic Symbol Resolver"]
    Type_Hierarchy_Reference_Engine["Type Hierarchy & Reference Engine"]
    LSP_Protocol_Orchestrator -- "filters results via semantic classification" --> Semantic_Symbol_Resolver
    LSP_Protocol_Orchestrator -- "triggers structural graph reconstruction" --> Type_Hierarchy_Reference_Engine
    Semantic_Symbol_Resolver -- "delegates JSON-RPC message framing and transport" --> LSP_Protocol_Orchestrator
    Type_Hierarchy_Reference_Engine -- "executes multi-stage structural queries" --> LSP_Protocol_Orchestrator
    Type_Hierarchy_Reference_Engine -- "shares symbol metadata for refinement" --> Semantic_Symbol_Resolver
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Implements the standard LSP feature set for synchronous semantic queries, translating high-level requests into protocol-compliant messages.

### LSP Protocol Orchestrator
Manages the low-level lifecycle of the Language Server connection, including session initialization, JSON-RPC message framing, and the synchronous request-response loop.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/hierarchy_builder.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/hierarchy_builder.py)
  - `static_analyzer.engine.hierarchy_builder.HierarchyBuilder.build` ([L34-L111](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/hierarchy_builder.py#L34-L111)) - Method
  - `static_analyzer.engine.hierarchy_builder.HierarchyBuilder._resolve_type_hierarchy_item` ([L113-L135](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/hierarchy_builder.py#L113-L135)) - Method


### Semantic Symbol Resolver
Implements core LSP navigation features to traverse source code, translating requests for symbol locations, definitions, and documentation into specific LSP method calls.


**Related Classes/Methods**:

- `static_analyzer.engine.lsp_client.LSPClient.definition`:325-339



**Source Files:**

- [`static_analyzer/engine/language_adapter.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/language_adapter.py)
  - `static_analyzer.engine.language_adapter.LanguageAdapter.is_class_like` ([L271-L272](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/language_adapter.py#L271-L272)) - Method
- [`static_analyzer/engine/lsp_client.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py)
  - `static_analyzer.engine.lsp_client.MethodNotFoundError` ([L27-L28](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L27-L28)) - Class
  - `static_analyzer.engine.lsp_client.LSPClient.definition` ([L325-L339](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L325-L339)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient.implementation` ([L350-L364](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L350-L364)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient._send_request` ([L541-L577](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L541-L577)) - Method


### Type Hierarchy & Reference Engine
Handles complex structural analysis by resolving inheritance patterns and refining raw reference data through multi-stage workflows and post-processing logic.


**Related Classes/Methods**:

- `static_analyzer.engine.lsp_client.LSPClient.type_hierarchy_prepare`:375-386
- `static_analyzer.engine.lsp_client.LSPClient.type_hierarchy_supertypes`:388-393



**Source Files:**

- [`static_analyzer/engine/lsp_client.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py)
  - `static_analyzer.engine.lsp_client.LSPClient.type_hierarchy_prepare` ([L375-L386](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L375-L386)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient.type_hierarchy_supertypes` ([L388-L393](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L388-L393)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient.type_hierarchy_subtypes` ([L395-L400](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L395-L400)) - Method
- [`static_analyzer/engine/symbol_table.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/symbol_table.py)
  - `static_analyzer.engine.symbol_table.SymbolTable.file_symbols` ([L52-L54](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/symbol_table.py#L52-L54)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)