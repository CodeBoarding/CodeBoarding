```mermaid
graph LR
    Agent_Orchestrator["Agent Orchestrator"]
    Context_Strategy_Engine["Context & Strategy Engine"]
    Architectural_Synthesizer["Architectural Synthesizer"]
    Functional_Analyzer["Functional Analyzer"]
    Structural_Verifier["Structural Verifier"]
    Prompt_Management_System["Prompt Management System"]
    Provider_Configuration["Provider Configuration"]
    Analysis_Data_Schema["Analysis Data Schema"]
    Agent_Orchestrator -- "initiates analysis by requesting project grounding and a prioritized inspection plan" --> Context_Strategy_Engine
    Context_Strategy_Engine -- "supplies project‑level bias and metadata for grouping clusters into logical components" --> Architectural_Synthesizer
    Architectural_Synthesizer -- "passes high‑level mental model to guide deep‑dive analysis of specific implementation details" --> Functional_Analyzer
    Functional_Analyzer -- "submits extracted implementation insights for validation against the ground‑truth CFG" --> Structural_Verifier
    Structural_Verifier -- "returns validated, high‑fidelity insights to be integrated into the final repository documentation" --> Agent_Orchestrator
    Agent_Orchestrator -- "retrieves specialized prompt templates tailored to the specific LLM provider being used" --> Prompt_Management_System
    Agent_Orchestrator -- "requests configured LLM client instances with appropriate temperature and token limits" --> Provider_Configuration
    Functional_Analyzer -- "utilizes structured Pydantic models to format implementation details for downstream consumption" --> Analysis_Data_Schema
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The intelligent core responsible for driving the code analysis and documentation generation using large language models. It orchestrates agent workflows, manages interactions with various tools, and structures the analysis insights.

### Agent Orchestrator
Central controller (CodeBoardingAgent) that manages the analysis lifecycle, maintains shared state, and sequences agent execution.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.CodeBoardingAgent`</a>


### Context & Strategy Engine
Combines metadata extraction (MetaAgent) and recursive planning (PlannerAgent) to determine analysis scope and priorities.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.MetaAgent`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.PlannerAgent`</a>


### Architectural Synthesizer
AbstractionAgent responsible for grouping files and clusters into high‑level logical components based on CFG patterns.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.AbstractionAgent`</a>


### Functional Analyzer
DetailsAgent that performs deep‑dives into specific subgraphs to extract implementation details and class/function relationships.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.DetailsAgent`</a>


### Structural Verifier
ValidationEngine that cross‑references LLM insights against raw CFG data to ensure architectural claims match the actual call graph.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.ValidationEngine`</a>


### Prompt Management System
PromptRegistry that provides model‑specific templates (Claude, GPT, Gemini) to ensure consistent LLM interactions.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.PromptRegistry`</a>


### Provider Configuration
Manages LLM initialization, provider‑specific settings (Ollama, Bedrock, etc.), and system‑wide constants.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.ProviderConfig`</a>


### Analysis Data Schema
Pydantic ResponseModels (e.g., AnalysisInsights) used for type‑safe communication between agents.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/gemini_flash_prompts.py" target="_blank" rel="noopener noreferrer">`agents.prompts.gemini_flash_prompts.AnalysisInsights`</a>




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)