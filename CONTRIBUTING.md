# CONTRIBUTING.md

CodeBoarding is an early-stage project, and we love contributors! :heart:

---

## 1) Before you start
- Check existing issues or open a new one to discuss your idea.
- Small, focused changes are preferred.
- Be kind and constructive in discussions (Discord welcome).

---

## 2) What to work on
- Bug fixes
- Features and enhancements
- Language support and static analysis improvements
- Docs and examples
- Performance and stability (LLM prompting mostly)

---

## 3) Code style (quick)
- **Naming:** variables use `snake_case`; classes use `PascalCase`.
- **Formatting:** We use Black formatter (line length: 120).
- Add type hints if possible.

---

## 4) Development setup (for contributors)

For active contributors who want to modify the code:

```bash
# Install with dev dependencies (includes Black formatter and pre-commit)
uv sync --dev

# Activate virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Run setup (this will automatically install pre-commit hooks if available)
python setup.py
```

The pre-commit hooks will automatically format your code with Black before each commit.

For regular users who just want to run the analysis, use `uv sync --frozen` instead (see README).

---

## 5) Adding a new language

Adding language support requires changes across several files. Use [PR #276 (Rust support)](https://github.com/CodeBoarding/CodeBoarding/pull/276) as a reference.

### 5a) Code changes (all required)

| File | What to add |
|------|-------------|
| `static_analyzer/engine/adapters/<lang>_adapter.py` | **New file.** Adapter class extending `LanguageAdapter`. Handles language-specific qualified name construction, reference keys, and any special rules (e.g. `mod.rs` collapsing for Rust). |
| `static_analyzer/engine/adapters/__init__.py` | Import the adapter and add it to `ADAPTER_REGISTRY`. |
| `static_analyzer/constants.py` | Add a value to the `Language` enum. Qualified names use the universal `.` delimiter defined in `ClusteringConfig.QUALIFIED_NAME_DELIMITER`. |
| `static_analyzer/__init__.py` | Add a mapping in `_lang_to_adapter_name()` from the `ProgrammingLanguage` name to the adapter registry key. |
| `vscode_constants.py` | Add an LSP server config entry to `VSCODE_CONFIG["lsp_servers"]` with the server name, command, languages, file extensions, and install command. |
| `tool_registry/registry.py` | Add a `ToolDependency` entry to `TOOL_REGISTRY` (see below). |

### 5b) Registering the LSP server dependency

The LSP server must be registered in `tool_registry/registry.py` so it gets installed automatically. The `ToolDependency` entry depends on how the server is distributed:

**npm package** (e.g. pyright, typescript-language-server) — no pipeline update needed:
```python
ToolDependency(
    key="python",
    binary_name="pyright-langserver",
    kind=ToolKind.NODE,
    config_section=ConfigSection.LSP_SERVERS,
    npm_packages=["pyright@1.1.400"],
    js_entry_file="langserver.index.js",
    js_entry_parent="pyright",
)
```

**Upstream download** (e.g. jdtls from Eclipse) — downloaded directly, no pipeline:
```python
ToolDependency(
    key="java",
    binary_name="java",
    kind=ToolKind.ARCHIVE,
    config_section=ConfigSection.LSP_SERVERS,
    source=UpstreamToolSource(
        tag=JDTLS_VERSION,
        url_template=JDTLS_URL_TEMPLATE,
        build=JDTLS_BUILD,
    ),
    archive_subdir="jdtls",
)
```

**Built from source** (e.g. gopls, tokei) — no upstream binaries, needs pipeline:
```python
ToolDependency(
    key="go",
    binary_name="gopls",
    kind=ToolKind.NATIVE,
    config_section=ConfigSection.LSP_SERVERS,
    source=GitHubToolSource(
        tag=TOOLS_TAG,
        repo=TOOLS_REPO,
        asset_template="gopls-{platform_suffix}",
        sha256={...},
    ),
)
```

### 5c) Build pipeline (only for `ToolKind.NATIVE` without upstream binaries)

If the LSP server doesn't publish pre-built binaries (like gopls or tokei), you need to update `.github/workflows/build-tools.yml` to build it from source. The workflow is manually triggered and publishes binaries to the [`CodeBoarding/tools`](https://github.com/CodeBoarding/tools) repo:

1. Add a new `build-<tool>` job to `build-tools.yml` with the appropriate toolchain (Go, Rust, etc.)
2. Add the tool version as a top-level `env:` variable
3. Add the new artifacts to the `publish-release` job
4. Run the workflow, then copy the SHA256 hashes from the output into your `ToolDependency`

### 5d) Tests

- Write unit tests for the adapter (aim for 100% coverage on new code)
- Run `uv run pytest --ignore=tests/integration` to verify nothing is broken
- Run `uv run mypy .` and `uv run black . --check`
- Add integration test fixtures:
  - **Edge cases** (`tests/integration/fixtures/edge_cases/<lang>_edge_cases.json`): A hand-crafted small project that exercises language-specific features (interfaces, generics, inheritance, etc.). Lists `expected_references` that the static analysis must find. See `go_edge_cases.json` or `python_edge_cases.json` for the format.
  - **Real project** (`tests/integration/fixtures/real_projects/<project>_<lang>.json`): A well-known open-source repo pinned to a specific commit, with expected metric counts (references, packages, call graph nodes/edges, source files). See `prometheus_go.json` or `mockito_java.json` for the format.

---

## 5e) C++ compilation-database generation (opt-in)

Clangd needs a `compile_commands.json` (or a `compile_flags.txt`) to index a C++ project. CMake/Meson/Ninja emit one with a single flag, but `Make`, `Autotools`, and `Bazel` don't — you either hand-write a CDB or use a helper tool.

CodeBoarding can drive those helpers for you, but only when you explicitly opt in:

```sh
export CODEBOARDING_CPP_GENERATE_CDB=1
```

With that set, `CppAdapter.prepare_project` runs the right generator based on marker files:

| Marker | Generator | Tools required on PATH |
|---|---|---|
| `Makefile` / `GNUmakefile` | Bear (wraps `make clean all`) | `bear` (3.x), `make` |
| `configure.ac` / `Makefile.am` | Bear with a preconfigure step (`autoreconf` → `./configure` out-of-tree → `bear -- make`) | `bear`, `make`, `autoreconf` if no `./configure` |
| `MODULE.bazel` / `WORKSPACE` | `bazel aquery` + JSON transform | `bazel` (6+) |

Generated `compile_commands.json` is written to `<repo>/.codeboarding/cdb/compile_commands.json`. The adapter passes `--compile-commands-dir` to clangd so this hidden location is found. A fingerprint file next to it caches the result — rerunning doesn't re-invoke the build unless the workspace/Makefile/configure inputs change.

Env vars (all optional):

| Var | Default | Meaning |
|---|---|---|
| `CODEBOARDING_CPP_GENERATE_CDB` | `0` | Master switch. Unset = no auto-generation. |
| `CODEBOARDING_CPP_GENERATOR_TIMEOUT` | `900` | Seconds before the build is killed. |
| `CODEBOARDING_CPP_MAKE_TARGET` | `clean all` | Targets passed after `--` to Bear. `clean` is in the default so Bear actually sees compile invocations (a warm tree emits an empty CDB). |
| `CODEBOARDING_CPP_CONFIGURE_ARGS` | empty | Shell-lexed flags forwarded to `./configure`. |
| `CODEBOARDING_CPP_BAZEL_QUERY` | `deps(//...)` | Scope for `bazel aquery 'mnemonic("CppCompile", …)'`. Narrow to skip vendored code. |

Explicitly **not** supported: MSBuild / Visual Studio solutions, Xcode project files. Convert to CMake first. C++20 modules work only to the extent clangd's experimental modules support works — expect rough edges.

Security note: the generators invoke real build systems, which execute arbitrary code defined by the project. The env-var opt-in is the consent point; never flip it on a repo you don't trust.

---

## 6) How to PR
- Fork the repo.
- Create a branch: `feat/...`, `fix/...`, or `docs/...`.
- In the PR, explain **WHAT/WHY/HOW**: what changed, why it's useful, and how you tested it.
- Optional: add a picture of a diagram from your favorite project.
