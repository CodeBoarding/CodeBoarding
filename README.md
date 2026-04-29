# CodeBoarding

See what your AI is building before it breaks.

CodeBoarding gives developers and coding agents a visual map of a codebase. It combines static analysis with LLM reasoning to generate architecture diagrams, component-level documentation, and navigable outputs you can use in your IDE, CI, and docs.

[Website](https://codeboarding.org) · [Open VSX extension](https://open-vsx.org/extension/CodeBoarding/codeboarding) · [Explore examples](https://codeboarding.org/diagrams) · [VS Code extension](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding) · [GitHub Action](https://github.com/marketplace/actions/codeboarding-diagram-first-documentation) ·[Discord](https://discord.gg/T5zHTJYFuy)

[![CodeBoarding demo](https://gist.githubusercontent.com/ivanmilevtues/1c4f921066613516cfd7b938014a6877/raw/611aec7711556807860ff2e1679a5dc4c0c23fed/CodeBoarding_extension_demo.gif)](https://open-vsx.org/extension/CodeBoarding/codeboarding)

Install the extension from Open VSX.

[![JavaScript](https://img.shields.io/badge/JavaScript-222222?style=flat-square&logo=javascript&logoColor=F7DF1E)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Java](https://img.shields.io/badge/Java-E76F00?style=flat-square&logo=openjdk&logoColor=white)](https://www.java.com/)
[![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Go](https://img.shields.io/badge/Go-00ADD8?style=flat-square&logo=go&logoColor=white)](https://go.dev/)
[![PHP](https://img.shields.io/badge/PHP-777BB4?style=flat-square&logo=php&logoColor=white)](https://www.php.net/)
[![Rust](https://img.shields.io/badge/Rust-000000?style=flat-square&logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![C#](https://custom-icon-badges.demolab.com/badge/C%23-512BD4.svg?style=flat-square&logo=cshrp&logoColor=white)](https://learn.microsoft.com/en-us/dotnet/csharp/)

## Few use cases:

- Keep architecture visible while agents code.
- Review AI-generated changes with system context before they turn into hidden debt.
- Understand large repositories faster with layered diagrams and component breakdowns.
- Share the same visual model across local workflows, IDEs, pull requests, and docs.

## What CodeBoarding generates

- High-level system architecture diagrams.
- Deeper component diagrams for important subsystems.
- Markdown documentation in `.codeboarding/`.
- Mermaid output that is easy to embed in docs and PRs.
- Incremental updates when only part of the codebase changes.

## How it works

```mermaid
graph LR
    Application_Orchestrator_Repository_Manager["Application Orchestrator & Repository Manager"]
    LLM_Agent_Core["LLM Agent Core"]
    Static_Code_Analyzer["Static Code Analyzer"]
    Agent_Tooling_Interface["Agent Tooling Interface"]
    Incremental_Analysis_Engine["Incremental Analysis Engine"]
    Documentation_Diagram_Generator["Documentation & Diagram Generator"]
    Application_Orchestrator_Repository_Manager -- "Orchestrator initiates analysis workflow, leveraging incremental updates based on detected code changes." --> Incremental_Analysis_Engine
    Application_Orchestrator_Repository_Manager -- "Orchestrator passes project context and triggers the main analysis workflow for the LLM Agent." --> LLM_Agent_Core
    Incremental_Analysis_Engine -- "Incremental engine requests static analysis for specific code segments (new or changed)." --> Static_Code_Analyzer
    Static_Code_Analyzer -- "Static analyzer provides analysis results to the incremental engine for caching." --> Incremental_Analysis_Engine
    LLM_Agent_Core -- "LLM Agent invokes specialized tools to interact with the codebase and analysis data." --> Agent_Tooling_Interface
    Agent_Tooling_Interface -- "Agent tools query the static analysis engine for detailed code insights." --> Static_Code_Analyzer
    Static_Code_Analyzer -- "Static analysis engine provides requested data to the agent tools." --> Agent_Tooling_Interface
    LLM_Agent_Core -- "LLM Agent delivers structured analysis insights for documentation and diagram generation." --> Documentation_Diagram_Generator
    click Application_Orchestrator_Repository_Manager href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Application_Orchestrator_Repository_Manager.md" "Details"
    click LLM_Agent_Core href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/LLM_Agent_Core.md" "Details"
    click Static_Code_Analyzer href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Static_Code_Analyzer.md" "Details"
    click Agent_Tooling_Interface href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Agent_Tooling_Interface.md" "Details"
    click Incremental_Analysis_Engine href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Incremental_Analysis_Engine.md" "Details"
    click Documentation_Diagram_Generator href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Documentation_Diagram_Generator.md" "Details"
```

For a deeper architecture walkthrough, see [`.codeboarding/overview.md`](.codeboarding/overview.md).

## Quick start

### Run from source

```bash
uv sync --frozen
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
python install.py
python main.py full --local /path/to/repo
```

### Use the packaged CLI

Requires **Python 3.12 or 3.13**. The recommended install method is [pipx](https://pipx.pypa.io), which keeps the CLI in its own isolated environment:

```bash
pipx install codeboarding --python python3.12
codeboarding-setup
codeboarding full --local /path/to/repo
```

Or, if you prefer pip, install into a virtual environment (not the global Python):

```bash
pip install codeboarding
codeboarding-setup
codeboarding full --local /path/to/repo
```

Output is written to `/path/to/repo/.codeboarding/`.

`python install.py` and `codeboarding-setup` download language server binaries to `~/.codeboarding/servers/`, shared across projects. Node.js (and its bundled `npm`) is required for the Python, TypeScript, JavaScript, and PHP language servers; if neither `node` nor `CODEBOARDING_NODE_PATH` is set, setup downloads a pinned Node.js runtime into `~/.codeboarding/servers/nodeenv/` automatically.

## Configuration

On first run, CodeBoarding creates `~/.codeboarding/config.toml`. Set one provider there or use environment variables.

```toml
[provider]
# openai_api_key            = "sk-..."
# anthropic_api_key         = "sk-ant-..."
# google_api_key            = "AIza..."
# vercel_api_key            = "vck_..."
# aws_bearer_token_bedrock  = "..."
# ollama_base_url           = "http://localhost:11434"
# openrouter_api_key        = "sk-..."

[llm]
# agent_model   = "gemini-3-flash"
# parsing_model = "gemini-3-flash"
```

Shell environment variables such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, and `OLLAMA_BASE_URL` take precedence over the config file. For private repositories, set `GITHUB_TOKEN` in your environment.

## Common commands

```bash
# Analyze a local repository
python main.py full --local ./my-project

# Increase diagram depth
python main.py full --local ./my-project --depth-level 2

# Re-analyze only changed parts when possible
python main.py incremental --local ./my-project

# Update a single component by ID
python main.py partial --local ./my-project --component-id "1.2"

# Analyze a remote GitHub repository
python main.py full https://github.com/pytorch/pytorch
```

## Where to use it

- [CLI](https://github.com/CodeBoarding/CodeBoarding) for local analysis, automation, and CI workflows.
- [VS Code extension](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding) for in-editor visual architecture.
- [GitHub Action](https://github.com/marketplace/actions/codeboarding-diagram-first-documentation) to keep diagrams updated in CI.

## Supported stack

- Languages: Python, TypeScript, JavaScript, Java, Go, PHP, Rust.
- LLM providers: OpenAI, Anthropic, Google, Vercel AI Gateway, AWS Bedrock, Ollama, OpenRouter, and more.

## Examples

- Visualized 800+ open-source repositories.
- Browse generated examples in [GeneratedOnBoardings](https://github.com/CodeBoarding/GeneratedOnBoardings).
- Try the hosted explorer at [codeboarding.org/diagrams](https://codeboarding.org/diagrams).

## Contributing

If you want to improve CodeBoarding, open an [issue](https://github.com/CodeBoarding/CodeBoarding/issues) or send a pull request. We welcome improvements to analysis quality, output generators, integrations, and developer experience.

## Vision

CodeBoarding is building an open standard for code understanding: a visual, accurate, high-level representation of a codebase that both humans and agents can use.
