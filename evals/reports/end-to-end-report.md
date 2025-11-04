# End-to-End Pipeline Evaluation

**Generated:** 2025-10-26T16:36:10.598456

**Evaluation Time:** 1906.51 seconds

### Summary

| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |
|---------|----------|--------|----------|--------------|------------|
| markitdown | Python | ✅ Success | 819.11 | 0 | 0 |
| tsoa | TypeScript | ✅ Success | 601.31 | 0 | 0 |
| cobra | Go | ✅ Success | 486.09 | 0 | 0 |

## Generated Top-Level Diagrams

### markitdown

```mermaid
graph LR
    Orchestrator["Orchestrator"]
    Document_Loader["Document Loader"]
    Vector_Store["Vector Store"]
    Unclassified["Unclassified"]
    Orchestrator -- "sends document to" --> Document_Loader
    Document_Loader -- "sends chunks to" --> Vector_Store
    Orchestrator -- "uses" --> Vector_Store
```

### tsoa

```mermaid
graph LR
    CLI_Configuration["CLI & Configuration"]
    Metadata_Generation_Parsing_Type_Resolution_["Metadata Generation (Parsing & Type Resolution)"]
    API_Definition_Builder_IR_["API Definition Builder (IR)"]
    Code_Generators["Code Generators"]
    Runtime_Integration_Layer["Runtime Integration Layer"]
    Unclassified["Unclassified"]
    CLI_Configuration -- "Triggers Parsing" --> Metadata_Generation_Parsing_Type_Resolution_
    CLI_Configuration -- "Provides Configuration" --> API_Definition_Builder_IR_
    CLI_Configuration -- "Provides Configuration" --> Code_Generators
    Metadata_Generation_Parsing_Type_Resolution_ -- "Provides AST & Resolved Types" --> API_Definition_Builder_IR_
    API_Definition_Builder_IR_ -- "Provides API IR" --> Code_Generators
    Code_Generators -- "Generates Runtime Code" --> Runtime_Integration_Layer
```

### cobra

```mermaid
graph LR
    Command_Tree_Lifecycle_Manager["Command Tree & Lifecycle Manager"]
    Flag_Configuration_Manager["Flag & Configuration Manager"]
    Argument_Parser_Validator["Argument Parser & Validator"]
    Help_Usage_System["Help & Usage System"]
    Shell_Completion_Engine["Shell Completion Engine"]
    Documentation_Generator["Documentation Generator"]
    Unclassified["Unclassified"]
    Command_Tree_Lifecycle_Manager -- "Configures Flags For" --> Flag_Configuration_Manager
    Command_Tree_Lifecycle_Manager -- "Processes Flag Input From" --> Flag_Configuration_Manager
    Command_Tree_Lifecycle_Manager -- "Defines Argument Structure For" --> Argument_Parser_Validator
    Command_Tree_Lifecycle_Manager -- "Receives Validated Arguments From" --> Argument_Parser_Validator
    Command_Tree_Lifecycle_Manager -- "Provides Metadata To" --> Help_Usage_System
    Command_Tree_Lifecycle_Manager -- "Provides Metadata To" --> Shell_Completion_Engine
    Command_Tree_Lifecycle_Manager -- "Provides Metadata To" --> Documentation_Generator
    Flag_Configuration_Manager -- "Provides Flag Details To" --> Help_Usage_System
    Flag_Configuration_Manager -- "Provides Flag Details To" --> Shell_Completion_Engine
    Flag_Configuration_Manager -- "Provides Flag Details To" --> Documentation_Generator
    Help_Usage_System -- "Accesses Command Properties From" --> Command_Tree_Lifecycle_Manager
    Help_Usage_System -- "Accesses Flag Properties From" --> Flag_Configuration_Manager
    Shell_Completion_Engine -- "Queries Command Structure From" --> Command_Tree_Lifecycle_Manager
    Shell_Completion_Engine -- "Queries Flag Definitions From" --> Flag_Configuration_Manager
    Documentation_Generator -- "Retrieves Command Details From" --> Command_Tree_Lifecycle_Manager
    Documentation_Generator -- "Retrieves Flag Details From" --> Flag_Configuration_Manager
```

## System Specifications

**Operating System:** Darwin (macOS-15.6-arm64-arm-64bit-Mach-O)
**Processor:** arm
**CPU Cores:** 10
**Git User:** brovatten
