```mermaid
graph LR
    Application_Orchestrator["Application Orchestrator"]
    Repository_Manager["Repository Manager"]
    Static_Analysis_Engine["Static Analysis Engine"]
    LLM_Prompt_Factory["LLM Prompt Factory"]
    LLM_Provider_Gateway["LLM Provider Gateway"]
    AI_Interpretation_Layer["AI Interpretation Layer"]
    Output_Generation_Engine["Output Generation Engine"]
    Unclassified["Unclassified"]
    User_External_Systems -- "Initiates Request" --> Application_Orchestrator
    Application_Orchestrator -- "Manages Repository Access" --> Repository_Manager
    Application_Orchestrator -- "Triggers Analysis" --> AI_Interpretation_Layer
    Application_Orchestrator -- "Coordinates Documentation Generation" --> Output_Generation_Engine
    Repository_Manager -- "Provides Source Code" --> Static_Analysis_Engine
    Static_Analysis_Engine -- "Delivers Enhanced Analysis Data" --> AI_Interpretation_Layer
    AI_Interpretation_Layer -- "Requests Formatted Prompts" --> LLM_Prompt_Factory
    LLM_Prompt_Factory -- "Returns Formatted Prompts" --> AI_Interpretation_Layer
    AI_Interpretation_Layer -- "Sends LLM Queries" --> LLM_Provider_Gateway
    LLM_Provider_Gateway -- "Returns LLM Responses" --> AI_Interpretation_Layer
    AI_Interpretation_Layer -- "Provides Architectural Insights" --> Output_Generation_Engine
    Output_Generation_Engine -- "Delivers Documentation & Diagrams" --> User_External_Systems
    click Application_Orchestrator href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Application_Orchestrator.md" "Details"
    click Repository_Manager href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Repository_Manager.md" "Details"
    click Static_Analysis_Engine href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Static_Analysis_Engine.md" "Details"
    click LLM_Prompt_Factory href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/LLM_Prompt_Factory.md" "Details"
    click LLM_Provider_Gateway href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/LLM_Provider_Gateway.md" "Details"
    click AI_Interpretation_Layer href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/AI_Interpretation_Layer.md" "Details"
    click Output_Generation_Engine href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Output_Generation_Engine.md" "Details"
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The system operates through a well-defined architectural flow, starting with the Application Orchestrator which serves as the central control unit, managing the overall execution, handling user requests, and coordinating the analysis and documentation generation processes. It now incorporates enhanced local execution and initialization capabilities. The Repository Manager provides the foundational raw source code by managing repository interactions. This code is then fed to the Static Analysis Engine, which performs in-depth static analysis, extracting crucial structural information, control flow graphs, and dependencies. A significant update to its LSP client now enables the delivery of richer, more detailed analysis data.

The core intelligence resides in the AI Interpretation Layer, which orchestrates AI-driven analysis, interprets responses from Large Language Models (LLMs), and derives architectural insights. This layer has been refactored for a more streamlined interaction with its internal tools. It dynamically interacts with the LLM Prompt Factory to generate tailored prompts and communicates with external LLMs via the LLM Provider Gateway to send queries and receive responses. Finally, the Output Generation Engine transforms these architectural insights into various consumable formats, including human-readable documentation and visual diagrams, with enhanced capabilities for MDX and Sphinx outputs. This comprehensive flow ensures a robust and adaptable system for architectural analysis and documentation.

### Application Orchestrator [[Expand]](./Application_Orchestrator.md)
The central control unit that manages the overall execution flow, handling repository input, orchestrating analysis, and coordinating documentation generation. It serves as the main entry point for the application, including enhanced local execution and initialization.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmain.py" target="_blank" rel="noopener noreferrer">`main.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinglocal_app.py" target="_blank" rel="noopener noreferrer">`local_app.py`</a>


### Repository Manager [[Expand]](./Repository_Manager.md)
Manages all interactions with code repositories, including cloning, reading files, and navigating the project structure. It provides the raw source code for analysis.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/__init__.py" target="_blank" rel="noopener noreferrer">`repo_utils/__init__.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/git_diff.py" target="_blank" rel="noopener noreferrer">`repo_utils/git_diff.py`</a>


### Static Analysis Engine [[Expand]](./Static_Analysis_Engine.md)
Performs in-depth static analysis on the codebase to extract structural information, control flow graphs (CFG), abstract syntax trees (AST), and dependencies. It leverages an enhanced Language Server Protocol (LSP) client for gathering detailed code information, improving the quality and type of data provided for analysis.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/__init__.py" target="_blank" rel="noopener noreferrer">`static_analyzer/__init__.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/scanner.py" target="_blank" rel="noopener noreferrer">`static_analyzer/scanner.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/graph.py" target="_blank" rel="noopener noreferrer">`static_analyzer/graph.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/lsp_client/client.py" target="_blank" rel="noopener noreferrer">`static_analyzer/lsp_client/client.py`</a>


### LLM Prompt Factory [[Expand]](./LLM_Prompt_Factory.md)
Dynamically generates and formats prompts tailored for various Large Language Models (LLMs) and specific analysis tasks.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/__init__.py" target="_blank" rel="noopener noreferrer">`agents/prompts/__init__.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/prompt_factory.py" target="_blank" rel="noopener noreferrer">`agents/prompts/prompt_factory.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/abstract_prompt_factory.py" target="_blank" rel="noopener noreferrer">`agents/prompts/abstract_prompt_factory.py`</a>


### LLM Provider Gateway [[Expand]](./LLM_Provider_Gateway.md)
Manages communication and interaction with external Large Language Model (LLM) providers, handling API calls and model selection.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/claude_prompts_bidirectional.py" target="_blank" rel="noopener noreferrer">`agents/prompts/claude_prompts_bidirectional.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gpt_prompts_unidirectional.py" target="_blank" rel="noopener noreferrer">`agents/prompts/gpt_prompts_unidirectional.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts_bidirectional.py" target="_blank" rel="noopener noreferrer">`agents/prompts/gemini_flash_prompts_bidirectional.py`</a>


### AI Interpretation Layer [[Expand]](./AI_Interpretation_Layer.md)
The core intelligence component that orchestrates AI-driven analysis, interprets LLM responses, and derives architectural insights. This layer utilizes various agents to perform its tasks, with agents/agent.py being central to its logic, and now features a streamlined or reorganized mechanism for agent tool interaction.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/__init__.py" target="_blank" rel="noopener noreferrer">`agents/__init__.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent.py" target="_blank" rel="noopener noreferrer">`agents/agent.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/meta_agent.py" target="_blank" rel="noopener noreferrer">`agents/meta_agent.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py" target="_blank" rel="noopener noreferrer">`agents/abstraction_agent.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py" target="_blank" rel="noopener noreferrer">`agents/details_agent.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/planner_agent.py" target="_blank" rel="noopener noreferrer">`agents/planner_agent.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validator_agent.py" target="_blank" rel="noopener noreferrer">`agents/validator_agent.py`</a>


### Output Generation Engine [[Expand]](./Output_Generation_Engine.md)
Transforms architectural insights into various consumable formats, including human-readable documentation and visual diagrams. It has enhanced capabilities for generating MDX and Sphinx outputs, improving flexibility and quality.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/__init__.py" target="_blank" rel="noopener noreferrer">`output_generators/__init__.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py" target="_blank" rel="noopener noreferrer">`output_generators/markdown.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py" target="_blank" rel="noopener noreferrer">`output_generators/html.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py" target="_blank" rel="noopener noreferrer">`output_generators/mdx.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py" target="_blank" rel="noopener noreferrer">`output_generators/sphinx.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/__init__.py" target="_blank" rel="noopener noreferrer">`diagram_analysis/__init__.py`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py" target="_blank" rel="noopener noreferrer">`diagram_analysis/diagram_generator.py`</a>


### Unclassified
Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)


**Related Classes/Methods**: _None_



### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
