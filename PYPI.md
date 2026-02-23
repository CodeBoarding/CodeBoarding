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

Language server binaries are downloaded automatically on first use. To pre-install them explicitly (useful in CI or restricted environments):

```bash
codeboarding-setup
```

> `npm` is required (used for Python, TypeScript, JavaScript, and PHP language servers). If `npm` is not found, it will be automatically installed during the setup. Binaries are stored in `~/.codeboarding/servers/` and shared across all projects.

---

## Quick Start

### CLI

```bash
# Analyze a local repository (output goes to /path/to/repo/.codeboarding/)
codeboarding --local /path/to/repo

# Analyze a remote GitHub repository (cloned to cwd/repo_name/, output to cwd/repo_name/.codeboarding/)
codeboarding https://github.com/user/repo
```

### Python API

```python
import json
from pathlib import Path
from diagram_analysis import DiagramGenerator, configure_models
from diagram_analysis.analysis_json import parse_unified_analysis

# Pass the key programmatically — shell env vars always take precedence if already set.
# Use the env-var name for whichever provider you want:
#   OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, OLLAMA_BASE_URL, …
configure_models(api_keys={"OPENAI_API_KEY": "sk-..."})

repo_path = Path("/path/to/repo")
output_dir = repo_path / ".codeboarding"
output_dir.mkdir(parents=True, exist_ok=True)

# Generate the architectural diagram
generator = DiagramGenerator(
    repo_location=repo_path,
    temp_folder=output_dir,
    repo_name="my-project",
    output_dir=output_dir,
    depth_level=1,
)
[analysis_path] = generator.generate_analysis()

# Read and inspect the results
with open(analysis_path) as f:
    data = json.load(f)

root, sub_analyses = parse_unified_analysis(data)

print(root.description)
for comp in root.components:
    print(f"  {comp.name}: {comp.description}")
    if comp.component_id in sub_analyses:
        for sub in sub_analyses[comp.component_id].components:
            print(f"    └ {sub.name}")
```

---

## Configuration

LLM provider keys and model overrides are stored in `~/.codeboarding/config.toml`, created automatically on first run:

```toml
# ~/.codeboarding/config.toml

[provider]
# Uncomment exactly one provider key
# openai_api_key    = "sk-..."
# anthropic_api_key = "sk-ant-..."
# google_api_key    = "AIza..."
# ollama_base_url   = "http://localhost:11434"

[llm]
# Optional: override the default model for your active provider
# agent_model   = "gemini-3-flash"
# parsing_model = "gemini-3-flash"
```

Shell environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) always take precedence over the config file, so CI/CD pipelines need no changes. For private repositories, set `GITHUB_TOKEN` in your environment.

> **Tip:** Google Gemini 3 Pro consistently produces the best diagram quality for complex codebases.

---

## CLI Reference

```
codeboarding [REPO_URL ...]           # remote: clone + analyze
codeboarding --local PATH             # local: analyze in-place
```

| Option | Description |
|---|---|
| `--local PATH` | Analyze a local repository (output: `PATH/.codeboarding/`) |
| `--depth-level INT` | Diagram depth (default: 1) |
| `--incremental` | Smart incremental update (only re-analyze changed files) |
| `--full` | Force full reanalysis, skip incremental detection |
| `--partial-component-id ID` | Update a single component by its ID |
| `--binary-location PATH` | Custom path to language server binaries (overrides `~/.codeboarding/servers/`) |
| `--upload` | Upload results to GeneratedOnBoardings repo (remote only) |
| `--enable-monitoring` | Enable run monitoring |

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
