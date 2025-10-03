```mermaid
graph LR
    Code_Scanner["Code Scanner"]
    Reference_Resolver["Reference Resolver"]
    LSP_Client_TypeScript_["LSP Client (TypeScript)"]
    Analysis_Result_Provider["Analysis Result Provider"]
    Unclassified["Unclassified"]
    Code_Scanner -- "provides parsed output to" --> Reference_Resolver
    Code_Scanner -- "contributes findings to" --> LSP_Client_TypeScript_
    Code_Scanner -- "provides data to" --> Analysis_Result_Provider
    Reference_Resolver -- "contributes findings to" --> LSP_Client_TypeScript_
    Reference_Resolver -- "provides data to" --> Analysis_Result_Provider
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The Static Analysis Engine subsystem is responsible for performing deep, language-specific analysis of source code, including reference resolution and integration with the VS Code environment. It processes raw source code and produces structured analysis results for downstream components.

### Code Scanner
This component performs the initial lexical and syntactical analysis of source code. It breaks down raw code into tokens and constructs an intermediate representation (e.g., an Abstract Syntax Tree), serving as the foundational input for further analysis.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainstatic_analyzer/scanner.py" target="_blank" rel="noopener noreferrer">`static_analyzer.scanner`</a>


### Reference Resolver
This component identifies and resolves symbolic references within the code, such as variable declarations, function calls, class definitions, and imports. It builds a comprehensive understanding of how different code elements relate to each other, crucial for semantic analysis.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainstatic_analyzer/reference_resolve_mixin.py" target="_blank" rel="noopener noreferrer">`static_analyzer.reference_resolve_mixin`</a>


### LSP Client (TypeScript)
This component acts as the interface for integrating the static analysis capabilities with the VS Code environment, specifically tailored for TypeScript. It communicates via the Language Server Protocol (LSP) to enable real-time code intelligence features like go-to-definition, hover information, and diagnostics.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainstatic_analyzer/lsp_client/typescript_client.py" target="_blank" rel="noopener noreferrer">`static_analyzer.lsp_client.typescript_client`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainvscode_constants.py" target="_blank" rel="noopener noreferrer">`vscode_constants`</a>


### Analysis Result Provider
This component is responsible for gathering and formatting the comprehensive output from the `Code Scanner` and `Reference Resolver`. It structures the analysis results into a consumable format specifically designed for subsequent processing by the `AI Interpretation Layer` to generate architectural insights and documentation.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/mainagents/abstraction_agent.py" target="_blank" rel="noopener noreferrer">`agents.abstraction_agent`</a>


### Unclassified
Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)


**Related Classes/Methods**: _None_



### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
