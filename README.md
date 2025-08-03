# <img src="./icon.svg" alt="CodeBoarding Logo" width="30" height="30" style="vertical-align: middle;"> CodeBoarding

![Supports Python 3 Projects](https://img.shields.io/badge/supports-Python%203.x-blue.svg) 
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Site: CodeBoarding.org](https://img.shields.io/badge/Site-CodeBoarding.org-5865F2)](https://codeboarding.org)
[![Join us on Discord](https://img.shields.io/badge/Discord-Join%20Us-5865F2?logo=discord&logoColor=white)](https://discord.gg/zaWg3Tfn)

**CodeBoarding** is an open-source codebase analysis tool that generates high-level diagram representations of codebases using static analysis and LLM agents, that humans and agents can interact with.  
It’s designed to support onboarding, documentation, and comprehension for large, complex systems.

- Extract modules and their relationships based on the control flow graph of the project.
- Builds different levels of abstraction with an LLM agent (multi-provider support) using remote and local inference.
- Outputs interactive diagrams (Mermaid.js) for integration into docs, IDEs, CI/CD.

📄 Existing visual generations: [GeneratedOnBoardings](https://github.com/CodeBoarding/GeneratedOnBoardings)  
🌐 Try for your open-source project: [www.codeboarding.org/demo](https://www.codeboarding.org/demo)

## 🧩 How it works

For detailed architecture information, see our [diagram documentation](.codeboarding/on_boarding).
```mermaid
graph LR
    Orchestration_Workflow["Orchestration & Workflow"]
    Static_Code_Analyzer["Static Code Analyzer"]
    AI_Analysis_Engine["AI Analysis Engine"]
    Analysis_Persistence["Analysis Persistence"]
    Output_Generator["Output Generator"]
    Orchestration_Workflow -- "invokes analysis on" --> Static_Code_Analyzer
    Static_Code_Analyzer -- "returns raw graph data to" --> Orchestration_Workflow
    Orchestration_Workflow -- "consults and saves analysis to" --> Analysis_Persistence
    Analysis_Persistence -- "provides cached analysis to" --> Orchestration_Workflow
    Orchestration_Workflow -- "invokes with graph data" --> AI_Analysis_Engine
    AI_Analysis_Engine -- "returns high-level model to" --> Orchestration_Workflow
    Orchestration_Workflow -- "sends model for rendering to" --> Output_Generator
    click Orchestration_Workflow href "https://github.com/CodeBoarding/CodeBoarding/tree/main/.codeboarding/Orchestration_Workflow.md" "Details"
    click Static_Code_Analyzer href "https://github.com/CodeBoarding/CodeBoarding/tree/main/.codeboarding/Static_Code_Analyzer.md" "Details"
    click AI_Analysis_Engine href "https://github.com/CodeBoarding/CodeBoarding/tree/main/.codeboarding/AI_Analysis_Engine.md" "Details"
```

## 📌 Setup

Setup the environment:

```bash
uv venv --python 3.11
uv pip sync requirements.txt
```



### Installing `pygraphviz` on macOS

If you hit errors installing `pygraphviz`, first install Graphviz and then point the build at Homebrew’s include/lib dirs:

```bash
brew install graphviz

export CPATH="$(brew --prefix)/include"
export LIBRARY_PATH="$(brew --prefix)/lib"

pip install pygraphviz \
  --global-option=build_ext \
  --global-option="-I$(brew --prefix)/include" \
  --global-option="-L$(brew --prefix)/lib"

```

### Environment Variables

```bash
# LLM Provider (choose one)
OPENAI_API_KEY=                 
ANTHROPIC_API_KEY=                 
GOOGLE_API_KEY=                  
AWS_BEARER_TOKEN_BEDROCK=
OLLAMA_BASE_URL=
OPENAI_BASE_URL=                   # Optional: Custom OpenAI endpoint

# Core Configuration
CACHING_DOCUMENTATION=false        # Enable/disable documentation caching
REPO_ROOT=./repos                  # Directory for downloaded repositories
ROOT_RESULT=./results              # Directory for generated outputs
PROJECT_ROOT=/path/to/CodeBoarding # Source project root (must end with /CodeBoarding)
DIAGRAM_DEPTH_LEVEL=1              # Max depth level for diagram generation

# Optional
GITHUB_TOKEN=                     # For accessing private repositories
LANGSMITH_TRACING=false           # Optional: Enable LangSmith tracing
LANGSMITH_ENDPOINT=               # Optional: LangSmith endpoint
LANGSMITH_PROJECT=                # Optional: LangSmith project name
LANGCHAIN_API_KEY=                # Optional: LangChain API key
```

> 💡 **Tip:** Our experience has shown that using **Google Gemini‑2.5‑Pro** yields the best results for complex diagram generation tasks.

### Run it

```bash
python demo.py <github_repo_url> --output-dir <output_path>
```

## 🖥️  Examples:

We have visualized **over 300+ popular open-source projects**. See examples:


### ChatTTS:
```mermaid
graph LR
    ChatTTS_Core_Orchestrator["ChatTTS Core Orchestrator"]
    Text_Processing_Module["Text Processing Module"]
    Speech_Synthesis_Models["Speech Synthesis Models"]
    Velocity_Inference_Engine["Velocity Inference Engine"]
    System_Utilities_Configuration["System Utilities & Configuration"]
    ChatTTS_Core_Orchestrator -- "Orchestrates Text Flow" --> Text_Processing_Module
    ChatTTS_Core_Orchestrator -- "Receives Processed Text" --> Text_Processing_Module
    ChatTTS_Core_Orchestrator -- "Orchestrates Synthesis Flow" --> Speech_Synthesis_Models
    ChatTTS_Core_Orchestrator -- "Receives Audio Output" --> Speech_Synthesis_Models
    ChatTTS_Core_Orchestrator -- "Initializes & Configures" --> System_Utilities_Configuration
    ChatTTS_Core_Orchestrator -- "Loads Assets" --> System_Utilities_Configuration
    Text_Processing_Module -- "Receives Raw Text" --> ChatTTS_Core_Orchestrator
    Text_Processing_Module -- "Provides Processed Text" --> ChatTTS_Core_Orchestrator
    Speech_Synthesis_Models -- "Receives Processed Data" --> ChatTTS_Core_Orchestrator
    Speech_Synthesis_Models -- "Generates Audio Output" --> ChatTTS_Core_Orchestrator
    Speech_Synthesis_Models -- "Delegates Inference To" --> Velocity_Inference_Engine
    Speech_Synthesis_Models -- "Receives Inference Results" --> Velocity_Inference_Engine
    Speech_Synthesis_Models -- "Utilizes GPU Resources" --> System_Utilities_Configuration
    Speech_Synthesis_Models -- "Accesses Model Config" --> System_Utilities_Configuration
    Velocity_Inference_Engine -- "Executes Model Inference" --> Speech_Synthesis_Models
    Velocity_Inference_Engine -- "Returns Inference Output" --> Speech_Synthesis_Models
    Velocity_Inference_Engine -- "Receives Engine Configuration" --> System_Utilities_Configuration
    System_Utilities_Configuration -- "Provides Assets & Config" --> ChatTTS_Core_Orchestrator
    System_Utilities_Configuration -- "Provides GPU & Config" --> Speech_Synthesis_Models
    System_Utilities_Configuration -- "Provides Engine Config" --> Velocity_Inference_Engine
    click ChatTTS_Core_Orchestrator href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/ChatTTS_Core_Orchestrator.md" "Details"
    click Text_Processing_Module href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/Text_Processing_Module.md" "Details"
    click Speech_Synthesis_Models href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/Speech_Synthesis_Models.md" "Details"
    click Velocity_Inference_Engine href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/Velocity_Inference_Engine.md" "Details"
    click System_Utilities_Configuration href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/System_Utilities_Configuration.md" "Details"
```


## PyTorch:
```mermaid
graph LR
    Core_Tensor_Operations["Core Tensor Operations"]
    Neural_Network_Construction["Neural Network Construction"]
    Automatic_Differentiation_Engine["Automatic Differentiation Engine"]
    Optimization_Algorithms["Optimization Algorithms"]
    Performance_Optimization["Performance Optimization"]
    Distributed_Training_Infrastructure["Distributed Training Infrastructure"]
    Model_Deployment_Optimization["Model Deployment & Optimization"]
    Meta_Programming_Code_Generation["Meta-Programming & Code Generation"]
    Functional_Programming_Transforms["Functional Programming Transforms"]
    Neural_Network_Construction -- "relies on" --> Core_Tensor_Operations
    Automatic_Differentiation_Engine -- "uses" --> Core_Tensor_Operations
    Automatic_Differentiation_Engine -- "uses" --> Neural_Network_Construction
    Optimization_Algorithms -- "optimizes" --> Neural_Network_Construction
    Optimization_Algorithms -- "uses" --> Automatic_Differentiation_Engine
    Performance_Optimization -- "optimizes" --> Core_Tensor_Operations
    Performance_Optimization -- "optimizes" --> Neural_Network_Construction
    Distributed_Training_Infrastructure -- "uses" --> Core_Tensor_Operations
    Distributed_Training_Infrastructure -- "uses" --> Automatic_Differentiation_Engine
    Distributed_Training_Infrastructure -- "uses" --> Neural_Network_Construction
    Model_Deployment_Optimization -- "optimizes" --> Neural_Network_Construction
    Model_Deployment_Optimization -- "optimizes" --> Core_Tensor_Operations
    Meta_Programming_Code_Generation -- "generates code for" --> Core_Tensor_Operations
    Meta_Programming_Code_Generation -- "generates code for" --> Neural_Network_Construction
    Functional_Programming_Transforms -- "uses" --> Core_Tensor_Operations
    Functional_Programming_Transforms -- "uses" --> Automatic_Differentiation_Engine
    click Core_Tensor_Operations href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Core Tensor Operations.md" "Details"
    click Neural_Network_Construction href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Neural Network Construction.md" "Details"
    click Automatic_Differentiation_Engine href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Automatic Differentiation Engine.md" "Details"
    click Optimization_Algorithms href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Optimization Algorithms.md" "Details"
    click Performance_Optimization href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Performance Optimization.md" "Details"
    click Distributed_Training_Infrastructure href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Distributed Training Infrastructure.md" "Details"
    click Model_Deployment_Optimization href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Model Deployment & Optimization.md" "Details"
    click Meta_Programming_Code_Generation href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Meta-Programming & Code Generation.md" "Details"
    click Functional_Programming_Transforms href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Functional Programming Transforms.md" "Details"
```

### FastAPI:
```mermaid
graph LR
    Application_Core["Application Core"]
    Middleware["Middleware"]
    Routing["Routing"]
    Request_Handling_Validation["Request Handling & Validation"]
    Dependency_Injection["Dependency Injection"]
    Security["Security"]
    Response_Handling["Response Handling"]
    API_Documentation["API Documentation"]
    Application_Core -- "sends request to" --> Middleware
    Middleware -- "forwards request to" --> Routing
    Routing -- "uses" --> Request_Handling_Validation
    Routing -- "uses" --> Dependency_Injection
    Routing -- "provides data for" --> Response_Handling
    Dependency_Injection -- "enables" --> Security
    Response_Handling -- "sends response to" --> Middleware
    API_Documentation -- "inspects" --> Routing
    API_Documentation -- "inspects" --> Request_Handling_Validation
    click Application_Core href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Application_Core.md" "Details"
    click Middleware href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Middleware.md" "Details"
    click Routing href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Routing.md" "Details"
    click Request_Handling_Validation href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Request_Handling_Validation.md" "Details"
    click Dependency_Injection href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Dependency_Injection.md" "Details"
    click Security href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Security.md" "Details"
    click API_Documentation href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/API_Documentation.md" "Details"
```


Browse more examples: [GeneratedOnBoardings Repository](https://github.com/CodeBoarding/GeneratedOnBoardings)

## 🚀 Integrations

Codeboarding is integrated with everything we use:

- 📦 [**VS Code Extension**](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding): Interact with the diagram direclty in your IDE.
- ⚙️ [**GitHub Action**](https://github.com/marketplace/actions/codeboarding-diagram-first-documentation): Automate diagram generation in CI/CD.
- 🔗 [**MCP Server**](https://github.com/CodeBoarding/CodeBoarding-MCP): Serves the consize documentation to your AI Agent assistant (ClaudeCode, VSCode, Cursor, etc.)

## 🤝 Contributing
We’re just getting started and would love your help!
If you have ideas, spot bugs, or want to improve anything - please [open an issue](https://github.com/CodeBoarding/CodeBoarding/issues) or tackle an existing one.
We actively track suggestions and welcome pull requests of all sizes.

## 🔮 Vision

**Unified high-level representation for codebases that is accurate** (hence static analysis). This representation is used by both people and agents → fully integrated in IDEs, MCP servers, and development workflows.
