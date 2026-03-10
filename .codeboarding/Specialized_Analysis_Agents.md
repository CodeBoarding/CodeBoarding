```mermaid
graph LR
    CodeBoarding_Orchestrator["CodeBoarding Orchestrator"]
    Abstraction_Agent["Abstraction Agent"]
    Details_Agent["Details Agent"]
    Metadata_Agent["Metadata Agent"]
    Response_Parser["Response Parser"]
    CodeBoarding_Orchestrator -- "initiates analysis tasks for" --> Abstraction_Agent
    CodeBoarding_Orchestrator -- "initiates analysis tasks for" --> Details_Agent
    CodeBoarding_Orchestrator -- "initiates analysis tasks for" --> Metadata_Agent
    CodeBoarding_Orchestrator -- "utilizes" --> Response_Parser
    Abstraction_Agent -- "utilizes" --> Response_Parser
    Details_Agent -- "utilizes" --> Response_Parser
    Metadata_Agent -- "utilizes" --> Response_Parser
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Collection of distinct agents (Metadata, Abstraction, Details) each focused on a specific phase or type of code analysis, leveraging LLMs to extract domain‑specific insights.

### CodeBoarding Orchestrator
Central control unit that initiates and manages the entire code analysis workflow, classifies files, dispatches tasks to specialized agents, and coordinates the overall process.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent.py" target="_blank" rel="noopener noreferrer">`agents.agent`</a>


### Abstraction Agent
Generates high-level summaries and conceptual groupings from code, receiving tasks from the Orchestrator and producing abstract representations of code structures and functionalities.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py" target="_blank" rel="noopener noreferrer">`agents.abstraction_agent`</a>


### Details Agent
Performs granular analysis of specific code segments or clusters, extracting fine-grained insights and detailed information under the direction of the Orchestrator.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py" target="_blank" rel="noopener noreferrer">`agents.details_agent`</a>


### Metadata Agent
Extracts and interprets project-level metadata such as project type, domain, technology stack, and architectural patterns, providing essential contextual information.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/meta_agent.py" target="_blank" rel="noopener noreferrer">`agents.meta_agent`</a>


### Response Parser
Utility component that interprets and standardizes structured responses from LLMs or external APIs, transforming raw outputs into usable data formats for the Orchestrator and agents.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent.py" target="_blank" rel="noopener noreferrer">`agents.agent._parse_response`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent.py" target="_blank" rel="noopener noreferrer">`agents.agent._try_parse`</a>




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)