# End-to-End Pipeline Evaluation

**Generated:** 2025-12-27T00:05:32.012040+00:00

### Summary

| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |
|---------|----------|--------|----------|--------------|------------|
| markitdown | Python | ✅ Success | 356.2 | 808,081 | 69 |
| codeboarding | Python | ✅ Success | 315.7 | 497,793 | 46 |
| django | Python | ❌ Failed | 1800.0 | 0 | 0 |

**Success:** 2/3
**Total Tokens:** 1,305,874
**Total Tool Calls:** 115

## Generated Top-Level Diagrams

### markitdown

```mermaid
graph LR
    Access_Protocol_Layer["Access & Protocol Layer"]
    MarkItDown_Core_Orchestrator["MarkItDown Core Orchestrator"]
    Office_Layout_Engines["Office & Layout Engines"]
    Binary_Cloud_Assisted_Parsers["Binary & Cloud-Assisted Parsers"]
    Web_Multimodal_Services["Web & Multimodal Services"]
    Data_Analysis_Translators["Data & Analysis Translators"]
    Mathematical_Syntax_Engine["Mathematical Syntax Engine"]
    Unclassified["Unclassified"]
    Access_Protocol_Layer -- "Triggers conversion pipeline by passing streams/URIs to the engine." --> MarkItDown_Core_Orchestrator
    MarkItDown_Core_Orchestrator -- "Delegates structural conversion tasks for Office and EPUB formats." --> Office_Layout_Engines
    MarkItDown_Core_Orchestrator -- "Offloads complex binary parsing and cloud extraction tasks." --> Binary_Cloud_Assisted_Parsers
    MarkItDown_Core_Orchestrator -- "Manages media transcription and web content scraping requests." --> Web_Multimodal_Services
    MarkItDown_Core_Orchestrator -- "Routes tabular and notebook data for structured text extraction." --> Data_Analysis_Translators
    Office_Layout_Engines -- "Invokes math translation utilities to convert OMML to LaTeX." --> Mathematical_Syntax_Engine
```

### codeboarding

```mermaid
graph LR
    Pipeline_Orchestrator["Pipeline Orchestrator"]
    Static_Fact_Extractor["Static Fact Extractor"]
    Repository_CFG_Manager["Repository & CFG Manager"]
    Cognitive_Agent_Fleet["Cognitive Agent Fleet"]
    Intelligence_Prompt_Infra["Intelligence & Prompt Infra"]
    Runtime_Tools_Monitoring["Runtime Tools & Monitoring"]
    Unclassified["Unclassified"]
    Pipeline_Orchestrator -- "Triggers the initial scanning phase to populate the analysis result cache by invoking LSP clients." --> Static_Fact_Extractor
    Pipeline_Orchestrator -- "Coordinates the agentic workflow, passing analysis results to synthesize high-level documentation." --> Cognitive_Agent_Fleet
    Static_Fact_Extractor -- "Persists extracted symbols and call graphs into structured models for later retrieval and cross-referencing." --> Repository_CFG_Manager
    Cognitive_Agent_Fleet -- "Uses the toolkit and monitoring callbacks to dynamically explore source files and track agent progress." --> Runtime_Tools_Monitoring
    Cognitive_Agent_Fleet -- "Requests context-aware prompts and validates structured LLM outputs against Pydantic schemas." --> Intelligence_Prompt_Infra
    Runtime_Tools_Monitoring -- "Provides agents with read access to the physical file system and the logical CFG graph nodes." --> Repository_CFG_Manager
```

### django

*No diagram generated for this project.*


## System Specifications

**Operating System:** Darwin (macOS-26.2-arm64-arm-64bit)
**Processor:** arm
**CPU Cores:** 14
**Git User:** ivanmilevtues
