```mermaid
graph LR
    LLM_Configuration_Manager["LLM Configuration Manager"]
    Prompt_Generation_System["Prompt Generation System"]
    Prompt_Generation_System -- "uses" --> LLM_Configuration_Manager
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Manages the generation of prompts for various Large Language Models (LLMs) and handles their configuration, including API key retrieval and argument resolution. It ensures that AI agents can correctly interact with different LLM providers.

### LLM Configuration Manager
This component is solely responsible for the configuration, initialization, and lifecycle management of various Large Language Model (LLM) instances. This includes retrieving and managing API keys, selecting the appropriate model, and resolving model-specific arguments and settings (e.g., temperature). It ensures that LLMs are properly set up and ready for interaction before consuming prompts.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py" target="_blank" rel="noopener noreferrer">`agents.llm_config.get_llm_api_key`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py" target="_blank" rel="noopener noreferrer">`agents.llm_config.resolve_llm_extra_args`</a>


### Prompt Generation System
This component manages the entire process of generating prompts for various Large Language Models (LLMs). It includes a `PromptFactory` that orchestrates the selection and instantiation of LLM-specific prompt factories (e.g., Claude, DeepSeek, Kimi, Gemini Flash, GLM, GPT). Its primary role is to provide specialized and correctly formatted prompt strings tailored to each LLM, guiding them effectively for codebase analysis tasks.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/prompt_factory.py" target="_blank" rel="noopener noreferrer">`agents.prompts.prompt_factory.LLMType`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/prompt_factory.py" target="_blank" rel="noopener noreferrer">`agents.prompts.prompt_factory.PromptFactory`</a>




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)
