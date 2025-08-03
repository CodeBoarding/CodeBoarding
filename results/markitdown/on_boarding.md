```mermaid
graph LR
    User_Interfaces_CLI_API_["User Interfaces (CLI & API)"]
    Core_Conversion_Engine_Facade_["Core Conversion Engine (Facade)"]
    Converter_Suite["Converter Suite"]
    External_Service_Integrations["External Service Integrations"]
    User_Interfaces_CLI_API_ -- "Initiates Conversion" --> Core_Conversion_Engine_Facade_
    Core_Conversion_Engine_Facade_ -- "Discovers & Registers" --> Converter_Suite
    Core_Conversion_Engine_Facade_ -- "Executes" --> Converter_Suite
    Converter_Suite -- "Delegates to" --> External_Service_Integrations
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/GeneratedOnBoardings)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/demo)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The analysis has been updated to merge the `Converter Registry & Plugin Loader` into the `Core Conversion Engine (Facade)`, simplifying the model and more accurately representing the system's architecture.

### User Interfaces (CLI & API)
Provides the primary entry points for end-users and other systems. This component is responsible for parsing user input and invoking the core conversion functionality.


**Related Classes/Methods**:

- `markitdown.__main__`
- `markitdown-mcp.__main__`


### Core Conversion Engine (Facade)
The central orchestration component, embodied by the `MarkItDown` class. It acts as a facade, simplifying the conversion process by managing the workflow, discovering and loading converter plugins, and selecting the appropriate converter for a given task.


**Related Classes/Methods**:

- <a href="https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/_markitdown.py#L92-L770" target="_blank" rel="noopener noreferrer">`markitdown._markitdown.MarkItDown` (92:770)</a>
- <a href="https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/_markitdown.py#L64-L81" target="_blank" rel="noopener noreferrer">`markitdown._markitdown.MarkItDown._load_plugins` (64:81)</a>


### Converter Suite
A collection of individual modules, each implementing the specific logic to convert a single file format (e.g., DOCX, PDF, XLSX) to Markdown. These are the workhorses of the system and contain the core transformation logic. They are designed to be pluggable.


**Related Classes/Methods**:

- `markitdown.converters`
- `markitdown-sample-plugin._plugin`


### External Service Integrations
A component that encapsulates connections to third-party AI/LLM services like Azure Document Intelligence or OpenAI. It is used by specific converters to offload complex tasks such as OCR, image captioning, or audio transcription.


**Related Classes/Methods**:

- `markitdown.converters._doc_intel_converter`
- `markitdown.converters._image_converter`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)