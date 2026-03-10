```mermaid
graph LR
    Analysis_Data_Models_ADM_["Analysis Data Models (ADM)"]
    LLM_Response_Formatter_LRF_["LLM Response Formatter (LRF)"]
    LLM_Base_Utilities_LBU_["LLM Base Utilities (LBU)"]
    Source_Code_Reference_Manager_SCRM_["Source Code Reference Manager (SCRM)"]
    Component_ID_Service_CIS_["Component ID Service (CIS)"]
    Analysis_Data_Models_ADM_ -- "provides data to" --> LLM_Response_Formatter_LRF_
    Analysis_Data_Models_ADM_ -- "stores references from" --> Source_Code_Reference_Manager_SCRM_
    Analysis_Data_Models_ADM_ -- "uses" --> Component_ID_Service_CIS_
    LLM_Response_Formatter_LRF_ -- "formats data from" --> Analysis_Data_Models_ADM_
    LLM_Response_Formatter_LRF_ -- "utilizes" --> LLM_Base_Utilities_LBU_
    LLM_Base_Utilities_LBU_ -- "provides services to" --> LLM_Response_Formatter_LRF_
    Source_Code_Reference_Manager_SCRM_ -- "provides references to" --> Analysis_Data_Models_ADM_
    Component_ID_Service_CIS_ -- "assigns IDs for" --> Analysis_Data_Models_ADM_
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Defines canonical data structures and schemas for representing analysis results, intermediate states, and architectural insights, ensuring consistent data exchange across the subsystem.

### Analysis Data Models (ADM)
Defines canonical data structures and schemas for representing analysis results, intermediate states, and architectural insights, ensuring consistent data exchange across the subsystem.


**Related Classes/Methods**: _None_

### LLM Response Formatter (LRF)
Converts structured analysis data into human‑readable or LLM‑consumable string formats. Manages representation of CFG‑specific components and clusters, ensuring complex data can be communicated to AI agents or documentation.


**Related Classes/Methods**: _None_

### LLM Base Utilities (LBU)
Provides foundational utilities for interacting with LLMs, including a base model for LLM responses and mechanisms for extracting relevant strings.


**Related Classes/Methods**:

- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.llm_utils.LLMBaseModel`</a>
- <a href="https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py" target="_blank" rel="noopener noreferrer">`repos.codeboarding.llm_utils.extractor_str`</a>


### Source Code Reference Manager (SCRM)
Manages references to specific parts of source code (file paths, line numbers) enabling agents to point to relevant snippets.


**Related Classes/Methods**: _None_

### Component ID Service (CIS)
Ensures unique identification and tracking of components by providing functions for assigning and hashing component IDs.


**Related Classes/Methods**: _None_



### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)