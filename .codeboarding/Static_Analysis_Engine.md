```mermaid
graph LR
    Agent["Agent"]
    Scanner["Scanner"]
    LSP_Client["LSP Client"]
    Reference_Resolver["Reference Resolver"]
    Graph_Builder["Graph Builder"]
    Programming_Language_Support["Programming Language Support"]
    Analysis_Result_Handler["Analysis Result Handler"]
    RepoIgnoreManager["RepoIgnoreManager"]
    Unclassified["Unclassified"]
    Analysis_Result_Handler -- "provides results to" --> Agent
    Agent -- "consumes results from" --> Analysis_Result_Handler
    Agent -- "uses" --> RepoIgnoreManager
    Scanner -- "provides input to" --> LSP_Client
    Scanner -- "uses" --> Programming_Language_Support
    RepoIgnoreManager -- "filters files for" --> Scanner
    LSP_Client -- "provides ASTs to" --> Graph_Builder
    LSP_Client -- "provides parsed info to" --> Reference_Resolver
    Reference_Resolver -- "provides resolved references to" --> Graph_Builder
    Graph_Builder -- "provides CFGs to" --> Analysis_Result_Handler
    Graph_Builder -- "uses" --> Programming_Language_Support
    Programming_Language_Support -- "configures" --> Scanner
    Programming_Language_Support -- "configures" --> Graph_Builder
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The system's core functionality revolves around a sophisticated static code analysis pipeline orchestrated by an intelligent `Agent`. The `Scanner` initiates the process, leveraging the `RepoIgnoreManager` for efficient file filtering and `Programming Language Support` for language-specific rules, to feed raw code into the `LSP Client`. The `LSP Client` then provides rich parsed data, including Abstract Syntax Trees (ASTs), to both the `Reference Resolver` and the `Graph Builder`. The `Reference Resolver`, with its enhanced capabilities, plays a crucial role in identifying and linking symbolic references, which are then integrated into the `Graph Builder` to construct comprehensive Control Flow Graphs (CFGs). `Programming Language Support` also guides the `Graph Builder` in this process, ensuring accurate graph construction. All analysis results, including CFGs, are meticulously managed by the `Analysis Result Handler`, which then provides these results to the `Agent`. The `Agent`, now encompassing a specialized `ValidatorAgent`, acts as the central orchestrator, consuming these detailed analysis results to interact with the codebase, process information, and generate responses, potentially utilizing an LLM for advanced reasoning.

### Agent
The `Agent` component, encompassing `CodeBoardingAgent` (from `agents/agent.py`) and the new `ValidatorAgent` (from `agents/validator_agent.py`), acts as the central orchestrator. It leverages static analysis results and a toolkit of specialized tools to interact with the codebase, process information using an LLM, and generate responses. The `ValidatorAgent` specifically handles validation tasks, adding a new stage or type of processing to the overall workflow. The `Agent` manages the overall workflow, including prompt processing, tool invocation, and response parsing.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent.py#L36-L341" target="_blank" rel="noopener noreferrer">`agents.agent.CodeBoardingAgent`:36-341</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validator_agent.py" target="_blank" rel="noopener noreferrer">`agents.validator_agent.ValidatorAgent`</a>


### Scanner
The `Scanner` initiates the process with lexical analysis, potentially on a filtered set of files determined by the `RepoIgnoreManager`.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/__init__.py" target="_blank" rel="noopener noreferrer">`Scanner`</a>


### LSP Client
The `LSP Client` then provides rich parsed information, including Abstract Syntax Trees (ASTs), by interacting with external Language Servers.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/lsp_client/client.py#L58-L1105" target="_blank" rel="noopener noreferrer">`LSPClient`:58-1105</a>


### Reference Resolver
The `Reference Resolver` component (from `static_analyzer/reference_resolve_mixin.py`) is crucial for identifying and linking symbolic references within the code. Its enhanced capabilities improve the accuracy, scope, and efficiency of reference resolution, thereby improving the quality of data fed to the `Graph Builder`.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/reference_resolve_mixin.py#L15-L151" target="_blank" rel="noopener noreferrer">`ReferenceResolver`:15-151</a>


### Graph Builder
Both the ASTs from the `LSP Client` and the resolved references from the `Reference Resolver` feed into the `Graph Builder`, which constructs Control Flow Graphs (CFGs).


**Related Classes/Methods**:

- `GraphBuilder`


### Programming Language Support
Throughout this process, the `Programming Language Support` component provides language-specific configurations and rules, ensuring accurate analysis.


**Related Classes/Methods**:

- `ProgrammingLanguageSupport`


### Analysis Result Handler
The `Analysis Result Handler` manages and stores the various outputs, such as CFGs and other static analysis results, making them accessible for further processing or consumption.


**Related Classes/Methods**:

- `AnalysisResultHandler`


### RepoIgnoreManager
The `RepoIgnoreManager` handles the logic for ignoring files and directories based on project-specific configurations, influencing the scope of analysis for other components.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py#L8-L107" target="_blank" rel="noopener noreferrer">`repo_utils.ignore.RepoIgnoreManager`:8-107</a>


### Unclassified
Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)


**Related Classes/Methods**: _None_



### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
