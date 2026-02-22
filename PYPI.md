# CodeBoarding

[![Website](https://img.shields.io/badge/Site-CodeBoarding.org-5865F2?style=for-the-badge&logoColor=white)](https://codeboarding.org)
[![Discord](https://img.shields.io/badge/Discord-Join%20Us-5865F2?style=for-the-badge&logoColor=white)](https://discord.gg/T5zHTJYFuy)
[![GitHub](https://img.shields.io/badge/GitHub-CodeBoarding-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/CodeBoarding/CodeBoarding)

**CodeBoarding** generates interactive architectural diagrams from any codebase using static analysis + LLM agents. It's built for developers and AI agents that need to understand large, complex systems quickly.

- Extracts modules and relationships via control flow graph analysis (LSP-based, no runtime required)
- Builds layered abstractions with an LLM agent (OpenAI, Anthropic, Google Gemini, Ollama, and more)
- Outputs Mermaid.js diagrams ready for docs, IDEs, and CI/CD pipelines

**Supported languages:** Python · TypeScript · JavaScript · Java · Go · PHP

---

## Installation

```bash
pip install codeboarding
```

After installing, run the setup script to download language server binaries:

```bash
python -m codeboarding.install
```

> The setup script installs LSP servers for static analysis. TypeScript/JavaScript support requires `npm` to be available. If `npm` is not found, those servers are skipped and can be installed manually later.

---

## Quick Start

### CLI

```bash
# Analyze a remote GitHub repository
codeboarding https://github.com/user/repo --output-dir ./docs

# Analyze a local repository
codeboarding --local /path/to/repo --project-name MyProject --output-dir ./docs
```

### Python API

```python
from pathlib import Path
from static_analyzer import get_static_analysis
from diagram_analysis import DiagramGenerator

repo_path = Path("/path/to/repo")
output_dir = Path("./docs")
output_dir.mkdir(parents=True, exist_ok=True)

# Step 1: Run static analysis (no LLM needed)
static_results = get_static_analysis(repo_path)

# Step 2: Generate diagrams (requires an LLM provider key)
generator = DiagramGenerator(
    repo_location=repo_path,
    temp_folder=output_dir,
    repo_name="my-project",
    output_dir=output_dir,
    depth_level=1,
)
generator.generate_analysis()
```

---

## Configuration

Set your LLM provider key in a `.env` file (or as environment variables):

```bash
# Choose one LLM provider
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
OLLAMA_BASE_URL=http://localhost:11434   # for local inference

# Core settings
REPO_ROOT=./repos                        # where cloned repos are stored
ROOT_RESULT=./results                    # where outputs are written
STATIC_ANALYSIS_CONFIG=./static_analysis_config.yml

# Optional
AGENT_MODEL=gemini-2.5-pro              # override the default model
GITHUB_TOKEN=ghp_...                    # for private repositories
```

> **Tip:** Google Gemini 2.5 Pro consistently produces the best diagram quality for complex codebases.

---

## CLI Reference

```
codeboarding [REPO_URL ...] [OPTIONS]
codeboarding --local PATH --project-name NAME [OPTIONS]
```

| Option | Description |
|---|---|
| `--local PATH` | Analyze a local repository instead of a remote one |
| `--project-name NAME` | Project name (required with `--local`) |
| `--output-dir PATH` | Directory for generated documentation |
| `--depth-level INT` | Diagram depth (default: 1) |
| `--no-cache-check` | Skip existing documentation check |
| `--partial-component NAME` | Update a single component in an existing analysis |
| `--partial-analysis NAME` | Analysis file to update (use with `--partial-component`) |

### Health checks (no LLM required)

```bash
codeboarding-health --local /path/to/repo --project-name MyProject --output-dir ./health
```

Runs structural checks (circular dependencies, unused exports, etc.) without calling any LLM.

---

## Integrations

- **[VS Code Extension](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding)** — browse diagrams directly in your IDE
- **[GitHub Action](https://github.com/marketplace/actions/codeboarding-diagram-first-documentation)** — generate docs on every push
- **[MCP Server](https://github.com/CodeBoarding/CodeBoarding-MCP)** — serve concise architecture docs to AI coding assistants (Claude Code, Cursor, etc.)

---

## Links

- [Source code](https://github.com/CodeBoarding/CodeBoarding)
- [Example diagrams (800+ open-source projects)](https://github.com/CodeBoarding/GeneratedOnBoardings)
- [Architecture documentation](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/overview.md)
- [Discord community](https://discord.gg/T5zHTJYFuy)
