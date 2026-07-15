```mermaid
graph LR
    Schema_to_Prompt_Protocol["Schema-to-Prompt Protocol"]
    Structured_Response_Parser["Structured Response Parser"]
    Schema_to_Prompt_Protocol -- "defines expected response schema and validation constraints" --> Structured_Response_Parser
    Structured_Response_Parser -- "provides feedback for prompt optimization and repair cycles" --> Schema_to_Prompt_Protocol
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Defines the foundational protocol for making Pydantic models compatible with LLM extraction, managing JSON schema generation and instruction strings.

### Schema-to-Prompt Protocol
Defines the foundational contract for serializing internal data structures into LLM-compatible schemas and managing instruction injection.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.CFGAnalysisInsights.llm_str` ([L710-L716](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L710-L716)) - Method


### Structured Response Parser
Handles the runtime extraction, validation, and re-hydration of LLM-generated data into the application's internal object model.


**Related Classes/Methods**: _None_


**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.CFGComponent.llm_str` ([L694-L701](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L694-L701)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)