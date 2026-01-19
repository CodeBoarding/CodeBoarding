# End-to-End Pipeline Evaluation

**Generated:** 2026-01-17T17:35:50.355961+00:00

### Summary

| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |
|---------|----------|--------|----------|--------------|------------|
| markitdown | Python | ✅ Success | 185.0 | 74,846 | 6 |
| codeboarding | Python | ✅ Success | 1010.6 | 319,883 | 65 |
| django | Python | ✅ Success | 1046.4 | 559,172 | 19 |

**Success:** 3/3
**Total Tokens:** 953,901
**Total Tool Calls:** 90

## Generated Top-Level Diagrams

### markitdown

```mermaid
graph LR
    Core_Conversion_Engine["Core Conversion Engine"]
    Document_Format_Converters["Document Format Converters"]
    DOCX_Advanced_Processing["DOCX Advanced Processing"]
    Markitdown_Control_Plane_MCP_Server["Markitdown Control Plane (MCP) Server"]
    Plugin_System["Plugin System"]
    Core_Conversion_Engine -- "orchestrates conversion using" --> Document_Format_Converters
    Core_Conversion_Engine -- "integrates with" --> Plugin_System
    Document_Format_Converters -- "utilizes for DOCX conversion" --> DOCX_Advanced_Processing
    Markitdown_Control_Plane_MCP_Server -- "invokes conversion services from" --> Core_Conversion_Engine
    Plugin_System -- "extends with new converters" --> Document_Format_Converters
```

### codeboarding

```mermaid
graph LR
    Codebase_Ingestion_Management["Codebase Ingestion & Management"]
    Static_Analysis_Engine["Static Analysis Engine"]
    AI_Orchestration_Prompt_Management["AI Orchestration & Prompt Management"]
    Analysis_Agents["Analysis Agents"]
    Output_Monitoring["Output & Monitoring"]
    Core_Data_Models["Core Data Models"]
    Codebase_Ingestion_Management -- "Provides raw source code and project configurations for analysis." --> Static_Analysis_Engine
    Static_Analysis_Engine -- "Delivers structured static analysis results (e.g., ASTs, symbol tables) to inform AI processing." --> AI_Orchestration_Prompt_Management
    AI_Orchestration_Prompt_Management -- "Orchestrates the execution of specialized agents and provides them with context and prompts." --> Analysis_Agents
    Analysis_Agents -- "Returns analysis insights and refined abstractions back to the orchestration layer." --> AI_Orchestration_Prompt_Management
    AI_Orchestration_Prompt_Management -- "Sends final analysis results and architectural insights for diagram generation and documentation." --> Output_Monitoring
    Static_Analysis_Engine -- "Populates and updates core data models (e.g., CallGraph, ASTNode) with analysis findings." --> Core_Data_Models
    Analysis_Agents -- "Utilizes and potentially updates core data models to represent architectural abstractions." --> Core_Data_Models
    Output_Monitoring -- "Consumes data from core data models to generate visualizations and documentation." --> Core_Data_Models
```

### django

```mermaid
graph LR
    HTTP_Session_Core["HTTP & Session Core"]
    ORM_Database_Layer["ORM & Database Layer"]
    Template_Forms_Engine["Template & Forms Engine"]
    Admin_Interface["Admin Interface"]
    Database_Migrations["Database Migrations"]
    Frontend_UI_Libraries["Frontend UI Libraries"]
    GIS_Spatial_Data["GIS & Spatial Data"]
    HTTP_Session_Core -- "Processes requests and renders responses using templates and forms." --> Template_Forms_Engine
    HTTP_Session_Core -- "Views (triggered by HTTP requests) interact with the ORM for data access." --> ORM_Database_Layer
    ORM_Database_Layer -- "Migrations define and apply schema changes for the ORM." --> Database_Migrations
    Template_Forms_Engine -- "Forms can integrate with client-side UI libraries for enhanced user experience." --> Frontend_UI_Libraries
    Admin_Interface -- "The Admin Interface manages data through the ORM." --> ORM_Database_Layer
    Admin_Interface -- "The Admin Interface uses Django's templating and forms for its UI." --> Template_Forms_Engine
    Admin_Interface -- "The Admin Interface often incorporates client-side UI libraries for interactive elements." --> Frontend_UI_Libraries
    ORM_Database_Layer -- "The ORM can define and interact with spatial data types provided by the GIS component." --> GIS_Spatial_Data
```


## System Specifications

**Operating System:** Darwin (macOS-26.2-arm64-arm-64bit)
**Processor:** arm
**CPU Cores:** 14
**Git User:** ivanmilevtues
