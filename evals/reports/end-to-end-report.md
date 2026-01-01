# End-to-End Pipeline Evaluation

**Generated:** 2025-12-27T14:15:51.644096+00:00

### Summary

| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |
|---------|----------|--------|----------|--------------|------------|
| markitdown | Python | ✅ Success | 390.4 | 1,653,294 | 72 |
| codeboarding | Python | ✅ Success | 6135.0 | 1,003,690 | 71 |
| django | Python | ❌ Failed | 6789.6 | 0 | 0 |

**Success:** 2/3
**Total Tokens:** 2,656,984
**Total Tool Calls:** 143

## Generated Top-Level Diagrams

### markitdown

```mermaid
graph LR
    Client_Integration_Interfaces["Client & Integration Interfaces"]
    MarkItDown_Orchestration_Core["MarkItDown Orchestration Core"]
    Office_Document_Processors["Office & Document Processors"]
    Web_AI_Intelligence_Subsystem["Web & AI Intelligence Subsystem"]
    Mathematical_Translation_Engine["Mathematical Translation Engine"]
    Unclassified["Unclassified"]
    Client_Integration_Interfaces -- "Initiates conversion requests with local paths or URIs" --> MarkItDown_Orchestration_Core
    MarkItDown_Orchestration_Core -- "Dispatches binary streams to specialized parsing modules" --> Office_Document_Processors
    MarkItDown_Orchestration_Core -- "Routes web content and media for intelligent transformation" --> Web_AI_Intelligence_Subsystem
    Office_Document_Processors -- "Delegates embedded formula conversion (OMML to LaTeX)" --> Mathematical_Translation_Engine
    Office_Document_Processors -- "Re-invokes orchestrator for nested/recursive content processing" --> MarkItDown_Orchestration_Core
    Web_AI_Intelligence_Subsystem -- "Utilizes shared LLM configuration for caption generation" --> MarkItDown_Orchestration_Core
```

### codeboarding

```mermaid
graph LR
    Pipeline_Orchestration_Subsystem["Pipeline & Orchestration Subsystem"]
    Static_Analysis_Context_Engine["Static Analysis & Context Engine"]
    Agent_Reasoning_Intelligence_Core["Agent Reasoning & Intelligence Core"]
    Autonomous_Analysis_Toolset["Autonomous Analysis Toolset"]
    Documentation_Diagram_Generator["Documentation & Diagram Generator"]
    Unclassified["Unclassified"]
    Pipeline_Orchestration_Subsystem -- "Triggers initial codebase scanning and populates the CallGraph to establish the ground truth." --> Static_Analysis_Context_Engine
    Static_Analysis_Context_Engine -- "Provides high-fidelity symbol and hierarchy context for tools in the CodeBoardingToolkit." --> Autonomous_Analysis_Toolset
    Autonomous_Analysis_Toolset -- "Inherits base ReAct logic and LLM provider abstractions to perform complex reasoning tasks." --> Agent_Reasoning_Intelligence_Core
    Agent_Reasoning_Intelligence_Core -- "Streams execution events, token usage, and performance metrics for real-time monitoring." --> Pipeline_Orchestration_Subsystem
    Autonomous_Analysis_Toolset -- "Executes deep-dive queries to refine architectural findings based on specific code structures." --> Static_Analysis_Context_Engine
    Pipeline_Orchestration_Subsystem -- "Directs the final rendering of discovered insights into human-readable documentation and visual diagrams." --> Documentation_Diagram_Generator
```

### django

*No diagram generated for this project.*


## System Specifications

**Operating System:** Darwin (macOS-26.2-arm64-arm-64bit)
**Processor:** arm
**CPU Cores:** 14
**Git User:** ivanmilevtues
