# <img src="./icon.svg" alt="CodeBoarding Logo" width="30" height="30" style="vertical-align: middle;"> CodeBoarding

CodeBoarding - Automated diagram code visualzation.
Generate accurate diagram representations of your project. To ensure accuracy and scalability we use both static analysis and LLM Agents.

🌐 **Website**: [www.codeboarding.org](https://www.codeboarding.org)

## 🚀 Try It Now

The **fastest and easiest** way to try CodeBoarding is through our online demo: [codeboarding.org/demo](https://www.codeboarding.org/demo)

### 🎯 Generations

Check out our generated onboarding documentation examples: [GeneratedOnBoardings Repository](https://github.com/CodeBoarding/GeneratedOnBoardings)

We've generated onboarding documentation for **300+ projects** - check if your favorite project is there!


## 🧱 Architecture

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

## Setup

Setup the environment:

```bash
uv venv --python 3.11
uv pip sync
```

### Environment Variables

You need **only one** API key from the supported LLM providers. We support:
- **OpenAI** (GPT-4o)
- **Anthropic** (Claude-3.5-Sonnet)
- **Google** (Gemini-2.5-Flash)
- **AWS Bedrock** (Claude-3.7-Sonnet)

Required environment variables:

```bash
# LLM Provider (choose one)
OPENAI_API_KEY=                    # OpenAI API key
ANTHROPIC_API_KEY=                 # Anthropic API key  
GOOGLE_API_KEY=                    # Google API key
AWS_BEARER_TOKEN_BEDROCK=          # AWS Bedrock token

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

### Compile the project for vscode extension:

```bash
pyinstaller --onefile vscode_runnable.py
```

Then the executable can be found in the `dist` folder.

## 🔮 Vision

**Unified high-level representation for codebases that is accurate** (hence static analysis). This representation is used by both people and agents → fully integrated in IDEs, MCP servers, and development workflows.

🔌 **Integrations**:
- [VS Code Extension](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding) - Generate onboarding docs directly in your IDE
- [GitHub Action](https://github.com/marketplace/actions/codeboarding-diagram-first-documentation) - Automate documentation generation in CI/CD
- [MCP Server](https://github.com/CodeBoarding/mcp-server) - Your vibe code assistant get latest docs for your dependencies in a concise matter which won't blow the context window
