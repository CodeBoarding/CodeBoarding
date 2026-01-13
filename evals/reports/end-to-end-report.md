# End-to-End Pipeline Evaluation

**Generated:** 2026-01-01T23:16:52.961160+00:00

### Summary

| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |
|---------|----------|--------|----------|--------------|------------|
| markitdown | Python | ✅ Success | 450.0 | 1,661,125 | 97 |
| codeboarding | Python | ✅ Success | 381.5 | 1,536,205 | 66 |
| django | Python | ❌ Failed | 53454.4 | 0 | 0 |

**Success:** 2/3
**Total Tokens:** 3,197,330
**Total Tool Calls:** 163

## Generated Top-Level Diagrams

### markitdown

```mermaid
graph LR
    Orchestration_Entry_Layer["Orchestration & Entry Layer"]
    Core_Framework_Plugin_System["Core Framework & Plugin System"]
    Structured_Document_Processors["Structured Document Processors"]
    AI_Media_Extraction_Services["AI & Media Extraction Services"]
    Math_Representation_Engine["Math Representation Engine"]
    Unclassified["Unclassified"]
    Orchestration_Entry_Layer -- "uses the core registration system and StreamInfo to manage available converters and metadata" --> Core_Framework_Plugin_System
    Orchestration_Entry_Layer -- "Dispatches standard document streams (DOCX, HTML) to specific processors based on detected MIME types" --> Structured_Document_Processors
    Orchestration_Entry_Layer -- "Routes binary or complex streams (PDF, Audio) to AI-enriched converters when higher fidelity extraction is required" --> AI_Media_Extraction_Services
    Structured_Document_Processors -- "invokes the math engine during pre-processing to translate embedded Office Math (OMML) into LaTeX" --> Math_Representation_Engine
    Structured_Document_Processors -- "inherit from the `DocumentConverter` base class to ensure a unified interface" --> Core_Framework_Plugin_System
    AI_Media_Extraction_Services -- "inherit from the `DocumentConverter` base class to ensure a unified interface" --> Core_Framework_Plugin_System
    AI_Media_Extraction_Services -- "utilize `StreamInfo` to manage multi-modal data streams during heavy processing tasks like transcription or OCR" --> Core_Framework_Plugin_System
```

### codeboarding

```mermaid
graph LR
    Job_Orchestration_Repo_Manager["Job Orchestration & Repo Manager"]
    LSP_Static_Analysis_Provider["LSP Static Analysis Provider"]
    Agent_Intelligence_Framework["Agent Intelligence Framework"]
    Multi_Stage_Analysis_Agents["Multi-Stage Analysis Agents"]
    Semantic_Graph_Output_Engine["Semantic Graph & Output Engine"]
    Unclassified["Unclassified"]
    Job_Orchestration -- "Triggers the initial multi-language scan to establish the semantic "ground truth" and symbol map." --> LSP_Static_Analysis_Provider
    Job_Orchestration -- "Orchestrates the agentic decomposition loop (Abstraction -> Details -> Validation) and manages parallel task execution." --> Multi_Stage_Analysis_Agents
    Multi_Stage_Analysis_Agents -- "Queries verified symbol data and call graphs (via the Toolkit) to ground LLM hallucinations in actual source code." --> LSP_Static_Analysis_Provider
    Multi_Stage_Analysis_Agents -- "Inherits core agentic capabilities and retrieves specialized prompt templates (e.g., `AbstractionAgent` uses `get_system_message`)." --> Agent_Intelligence_Framework
    Multi_Stage_Analysis_Agents -- "Passes structured analysis insights (`AnalysisInsights`) to the clustering and rendering pipeline." --> Semantic_Graph_Output_Engine
    Semantic_Graph_Output_Engine -- "Resolves call graph references to validate the generated architecture and cluster boundaries." --> LSP_Static_Analysis_Provider
```

### django

*No diagram generated for this project.*


## System Specifications

**Operating System:** Darwin (macOS-26.2-arm64-arm-64bit)
**Processor:** arm
**CPU Cores:** 14
**Git User:** ivanmilevtues
