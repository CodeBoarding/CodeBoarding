# End-to-End Pipeline Evaluation

**Generated:** 2025-12-22T18:00:22.916882+00:00

### Summary

| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |
|---------|----------|--------|----------|--------------|------------|
| markitdown | Python | ✅ Success | 28.4 | 0 | 0 |

**Success:** 1/1
**Total Tokens:** 0
**Total Tool Calls:** 0

## Generated Top-Level Diagrams

### markitdown

```mermaid
graph LR
    User_Interface_CLI_API_["User Interface (CLI/API)"]
    Conversion_Orchestrator["Conversion Orchestrator"]
    Document_Converters["Document Converters"]
    Intermediate_Document_Representation_IDR_["Intermediate Document Representation (IDR)"]
    Plugin_Management_System["Plugin Management System"]
    External_Services["External Services"]
    Unclassified["Unclassified"]
    User_Interface_CLI_API_ -- "initiates conversion requests to" --> Conversion_Orchestrator
    Conversion_Orchestrator -- "orchestrates conversion using" --> Document_Converters
    Document_Converters -- "parse input into" --> Intermediate_Document_Representation_IDR_
    Conversion_Orchestrator -- "requests plugin application from" --> Plugin_Management_System
    Plugin_Management_System -- "processes" --> Intermediate_Document_Representation_IDR_
    Plugin_Management_System -- "can interact with" --> External_Services
    Document_Converters -- "write converted documents to" --> External_Services
```


## System Specifications

**Operating System:** Darwin (macOS-15.6-arm64-arm-64bit-Mach-O)
**Processor:** arm
**CPU Cores:** 10
**Git User:** brovatten
