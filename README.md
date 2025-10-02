# <img src="./icon.svg" alt="CodeBoarding Logo" width="30" height="30" style="vertical-align: middle;"> CodeBoarding

![Support TypeScript 3 Projects](https://img.shields.io/badge/supports-TypeScript.x-blue.svg)
![Supports Python 3 Projects](https://img.shields.io/badge/supports-Python%203.x-blue.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Site: CodeBoarding.org](https://img.shields.io/badge/Site-CodeBoarding.org-5865F2)](https://codeboarding.org)
[![Join us on Discord](https://img.shields.io/badge/Discord-Join%20Us-5865F2?logo=discord&logoColor=white)](https://discord.gg/T5zHTJYFuy)

**CodeBoarding** is an open-source codebase analysis tool that generates high-level diagram representations of codebases
using static analysis and LLM agents, that humans and agents can interact with.  
It’s designed to support onboarding, documentation, and comprehension for large, complex systems.

- Extract modules and their relationships based on the control flow graph of the project.
- Builds different levels of abstraction with an LLM agent (multi-provider support) using remote or local inference.
- Outputs interactive diagrams (Mermaid.js) for integration into docs, IDEs, CI/CD.

📄 Existing visual generations: [GeneratedOnBoardings](https://github.com/CodeBoarding/GeneratedOnBoardings)  
🌐 Try for your open-source project: [www.codeboarding.org/diagrams](https://www.codeboarding.org/diagrams)

## 🧩 How it works

For detailed architecture information, see our [diagram documentation](.codeboarding/overview.md).

```mermaid
graph LR
    API_Service["API Service"]
    Job_Database["Job Database"]
    Orchestration_Engine["Orchestration Engine"]
    Repository_Manager["Repository Manager"]
    Static_Analysis_Engine["Static Analysis Engine"]
    AI_Interpretation_Layer["AI Interpretation Layer"]
    Output_Generation_Engine["Output Generation Engine"]
    Unclassified["Unclassified"]
    API_Service -- " Initiates Job " --> Job_Database
    API_Service -- " Triggers Analysis " --> Orchestration_Engine
    Orchestration_Engine -- " Manages Job State " --> Job_Database
    Orchestration_Engine -- " Requests Code " --> Repository_Manager
    Repository_Manager -- " Provides Code " --> Orchestration_Engine
    Orchestration_Engine -- " Requests Static Analysis " --> Static_Analysis_Engine
    Static_Analysis_Engine -- " Provides Richer Analysis Results " --> Orchestration_Engine
    Orchestration_Engine -- " Feeds Rich Analysis Data " --> AI_Interpretation_Layer
    AI_Interpretation_Layer -- " Returns Enhanced Architectural Insights " --> Orchestration_Engine
    AI_Interpretation_Layer -- " Queries Diff " --> Repository_Manager
    Orchestration_Engine -- " Passes Enhanced Insights for Generation " --> Output_Generation_Engine
    Output_Generation_Engine -- " Delivers Documentation " --> API_Service
    click Job_Database href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Job_Database.md" "Details"
    click Orchestration_Engine href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Orchestration_Engine.md" "Details"
    click Repository_Manager href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Repository_Manager.md" "Details"
    click Static_Analysis_Engine href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Static_Analysis_Engine.md" "Details"
    click AI_Interpretation_Layer href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/AI_Interpretation_Layer.md" "Details"
    click Output_Generation_Engine href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Output_Generation_Engine.md" "Details"
```

## 📌 Setup

Setup the environment:

```bash
python -m pip install uv==0.8.22
uv sync --frozen
source .venv/bin/activate
python setup.py
```

> [!IMPORTANT]  
> The setup script installs a language server for Python and TypeScript/JavaScript. In order to successfully install
> the TypeScript Language Server, you need to have `npm` installed. If `npm` is not found, the script will skip the
> installation of the TypeScript Language Server and you will need to install it manually later if you want to analyze
> TypeScript/JavaScript projects.


Configure the environment variables in a `.env` file (you can copy from `.env.example`):
The `python setup.py` command creates a `.env` file if it doesn't exist with a default value for `REPO_ROOT` and `ROOT_RESULT` as well as OLLAMA_BASE_URL for local LLM inference. If you want to use a different LLM provider, you need to set the corresponding API key in the `.env` file.

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
STATIC_ANALYSIS_CONFIG=./static_analysis_config.yml # Path to static analysis config

# Optional
GITHUB_TOKEN=                     # For accessing private repositories
LANGSMITH_TRACING=false           # Optional: Enable LangSmith tracing
LANGSMITH_ENDPOINT=               # Optional: LangSmith endpoint
LANGSMITH_PROJECT=                # Optional: LangSmith project name
LANGCHAIN_API_KEY=                # Optional: LangChain API key
```

> 💡 **Tip:** Our experience has shown that using **Google Gemini‑2.5‑Pro** yields the best results for complex diagram
> generation tasks.

### Run it

```bash
python demo.py <github_repo_url> --output-dir <output_path>
```

## 🖥️ Examples:

We have visualized **over 800+ popular open-source projects**. See examples:

## PyTorch:

```mermaid
graph LR
    Tensor_Operations_Core["Tensor Operations & Core"]
    Automatic_Differentiation_Autograd_Engine_["Automatic Differentiation (Autograd Engine)"]
    Neural_Network_Modules_torch_nn_["Neural Network Modules (torch.nn)"]
    Optimizers_torch_optim_["Optimizers (torch.optim)"]
    Data_Utilities_torch_utils_data_["Data Utilities (torch.utils.data)"]
    JIT_Compiler_Scripting_TorchScript_["JIT Compiler & Scripting (TorchScript)"]
    Hardware_Backends["Hardware Backends"]
    Data_Utilities_torch_utils_data_ -- " provides data to " --> Tensor_Operations_Core
    Tensor_Operations_Core -- " provides primitives for " --> Neural_Network_Modules_torch_nn_
    Tensor_Operations_Core -- " leverages " --> Hardware_Backends
    Neural_Network_Modules_torch_nn_ -- " performs operations on " --> Tensor_Operations_Core
    Neural_Network_Modules_torch_nn_ -- " operations recorded by " --> Automatic_Differentiation_Autograd_Engine_
    Neural_Network_Modules_torch_nn_ -- " exported to " --> JIT_Compiler_Scripting_TorchScript_
    Automatic_Differentiation_Autograd_Engine_ -- " computes gradients for " --> Optimizers_torch_optim_
    Optimizers_torch_optim_ -- " updates parameters of " --> Neural_Network_Modules_torch_nn_
    Hardware_Backends -- " executes computations for " --> Tensor_Operations_Core
    click Tensor_Operations_Core href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Tensor_Operations_Core.md" "Details"
    click Automatic_Differentiation_Autograd_Engine_ href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Automatic_Differentiation_Autograd_Engine_.md" "Details"
    click Neural_Network_Modules_torch_nn_ href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Neural_Network_Modules_torch_nn_.md" "Details"
    click Optimizers_torch_optim_ href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Optimizers_torch_optim_.md" "Details"
    click Data_Utilities_torch_utils_data_ href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Data_Utilities_torch_utils_data_.md" "Details"
    click JIT_Compiler_Scripting_TorchScript_ href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/JIT_Compiler_Scripting_TorchScript_.md" "Details"
    click Hardware_Backends href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/pytorch/Hardware_Backends.md" "Details"
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
    Application_Core -- " sends request to " --> Middleware
    Middleware -- " forwards request to " --> Routing
    Routing -- " uses " --> Request_Handling_Validation
    Routing -- " uses " --> Dependency_Injection
    Routing -- " provides data for " --> Response_Handling
    Dependency_Injection -- " enables " --> Security
    Response_Handling -- " sends response to " --> Middleware
    API_Documentation -- " inspects " --> Routing
    API_Documentation -- " inspects " --> Request_Handling_Validation
    click Application_Core href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Application_Core.md" "Details"
    click Middleware href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Middleware.md" "Details"
    click Routing href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Routing.md" "Details"
    click Request_Handling_Validation href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Request_Handling_Validation.md" "Details"
    click Dependency_Injection href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Dependency_Injection.md" "Details"
    click Security href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/Security.md" "Details"
    click API_Documentation href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/fastapi/API_Documentation.md" "Details"
```

### ChatTTS:

```mermaid
graph LR
    ChatTTS_Core_Orchestrator["ChatTTS Core Orchestrator"]
    Text_Processing_Module["Text Processing Module"]
    Speech_Synthesis_Models["Speech Synthesis Models"]
    Velocity_Inference_Engine["Velocity Inference Engine"]
    System_Utilities_Configuration["System Utilities & Configuration"]
    ChatTTS_Core_Orchestrator -- " Orchestrates Text Flow " --> Text_Processing_Module
    ChatTTS_Core_Orchestrator -- " Receives Processed Text " --> Text_Processing_Module
    ChatTTS_Core_Orchestrator -- " Orchestrates Synthesis Flow " --> Speech_Synthesis_Models
    ChatTTS_Core_Orchestrator -- " Receives Audio Output " --> Speech_Synthesis_Models
    ChatTTS_Core_Orchestrator -- " Initializes & Configures " --> System_Utilities_Configuration
    ChatTTS_Core_Orchestrator -- " Loads Assets " --> System_Utilities_Configuration
    Text_Processing_Module -- " Receives Raw Text " --> ChatTTS_Core_Orchestrator
    Text_Processing_Module -- " Provides Processed Text " --> ChatTTS_Core_Orchestrator
    Speech_Synthesis_Models -- " Receives Processed Data " --> ChatTTS_Core_Orchestrator
    Speech_Synthesis_Models -- " Generates Audio Output " --> ChatTTS_Core_Orchestrator
    Speech_Synthesis_Models -- " Delegates Inference To " --> Velocity_Inference_Engine
    Speech_Synthesis_Models -- " Receives Inference Results " --> Velocity_Inference_Engine
    Speech_Synthesis_Models -- " Utilizes GPU Resources " --> System_Utilities_Configuration
    Speech_Synthesis_Models -- " Accesses Model Config " --> System_Utilities_Configuration
    Velocity_Inference_Engine -- " Executes Model Inference " --> Speech_Synthesis_Models
    Velocity_Inference_Engine -- " Returns Inference Output " --> Speech_Synthesis_Models
    Velocity_Inference_Engine -- " Receives Engine Configuration " --> System_Utilities_Configuration
    System_Utilities_Configuration -- " Provides Assets & Config " --> ChatTTS_Core_Orchestrator
    System_Utilities_Configuration -- " Provides GPU & Config " --> Speech_Synthesis_Models
    System_Utilities_Configuration -- " Provides Engine Config " --> Velocity_Inference_Engine
    click ChatTTS_Core_Orchestrator href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/ChatTTS_Core_Orchestrator.md" "Details"
    click Text_Processing_Module href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/Text_Processing_Module.md" "Details"
    click Speech_Synthesis_Models href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/Speech_Synthesis_Models.md" "Details"
    click Velocity_Inference_Engine href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/Velocity_Inference_Engine.md" "Details"
    click System_Utilities_Configuration href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main//ChatTTS/System_Utilities_Configuration.md" "Details"
```

Browse more examples: [GeneratedOnBoardings Repository](https://github.com/CodeBoarding/GeneratedOnBoardings)

## 🚀 Integrations

Codeboarding is integrated with everything we use:

- 📦 [**VS Code Extension**](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding): Interact
  with the diagram directly in your IDE.
- ⚙️ [**GitHub Action**](https://github.com/marketplace/actions/codeboarding-diagram-first-documentation): Automate
  diagram generation in CI/CD.
- 🔗 [**MCP Server**](https://github.com/CodeBoarding/CodeBoarding-MCP): Serves the consize documentation to your AI
  Agent assistant (ClaudeCode, VSCode, Cursor, etc.)

## 🤝 Contributing

We’re just getting started and would love your help!
If you have ideas, spot bugs, or want to improve anything -
please [open an issue](https://github.com/CodeBoarding/CodeBoarding/issues) or tackle an existing one.
We actively track suggestions and welcome pull requests of all sizes.

## 🔮 Vision

**Unified high-level representation for codebases that is accurate** (hence static analysis). This representation is
used by both people and agents → fully integrated in IDEs, MCP servers, and development workflows.
