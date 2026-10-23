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

For a zero-setup environment, see [Dev Container / GitHub Codespaces](#dev-container--github-codespaces-zero-setup) below. Otherwise, set up locally:

### Dev Container / GitHub Codespaces (zero-setup)

Open the repo with VS Code's "Reopen in Container" or the Codespaces "Code" button. The post-create script runs `uv sync --dev`, `codeboarding-setup`, installs the pre-commit hook, and installs the Claude Code CLI. The default config (`.devcontainer/devcontainer.json`) has no host mounts and is Codespaces-safe; to bind-mount `~/.claude` and `~/.ssh/known_hosts` locally, select "CodeBoarding (local, host mounts)" in the VS Code picker, or use `--config .devcontainer/local/devcontainer.json` with the CLI.

- `codeboarding` commands always use the latest source (editable install).
- `~/.codeboarding` persists in a named volume, so language servers and `config.toml` survive rebuilds. To remove it, find it with `docker volume ls | grep codeboarding-` and delete it with `docker volume rm`.
- Telemetry is off (`DO_NOT_TRACK=1`).
- CI (`.github/workflows/devcontainer.yml`) builds the container on PRs and pushes to `main` that touch `.devcontainer/**`, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, or `tests/test_devcontainer.py`, using `CODEBOARDING_DEVCONTAINER_CI=1` to skip the language-server download and Claude CLI install.
- Java, .NET, Go and Rust toolchains are not included, so those languages show as unavailable.
- Apple Silicon (arm64): `codeboarding full` does not work (tokei/gopls are x86_64-only); tests, mypy and black work fine.
- LLM provider key: set it in `~/.codeboarding/config.toml` (persisted) or as a Codespaces secret.

#### Claude Code inside the container (local Dev Containers only)

> **GitHub Codespaces:** the default config has no host mounts and works in Codespaces. Run `claude auth login` and set up SSH inside the container.

The Claude Code extension (`anthropic.claude-code`) is installed automatically. The local config (`.devcontainer/local/devcontainer.json`) bind-mounts `~/.claude` and your SSH `known_hosts` file from the host. Private keys are never mounted; SSH uses agent forwarding.

| Host path | Container path | Purpose |
| --- | --- | --- |
| `~/.claude` | `/home/vscode/.claude` | Claude Code settings, memory, and skills |
| `~/.ssh/known_hosts` | `/etc/ssh/ssh_known_hosts` | GitHub host keys (read-only, no secrets) |

**Note:** `.devcontainer/local/devcontainer-lock.json` is a copy of `.devcontainer/devcontainer-lock.json` — update both when bumping feature versions.

**SSH access**

Private keys stay on your host; the container uses SSH agent forwarding.

- **Keys:** load them into your host agent with `ssh-add ~/.ssh/id_ed25519` and verify with `ssh-add -l`.
- **VS Code:** the agent is forwarded automatically — nothing to configure.
- **devcontainer CLI (macOS/Docker Desktop):**
  ```bash
  SOCK=/run/host-services/ssh-auth.sock  # Linux: SOCK="$SSH_AUTH_SOCK"
  devcontainer up --workspace-folder . --config .devcontainer/local/devcontainer.json \
    --mount "type=bind,source=$SOCK,target=/ssh-agent"
  devcontainer exec --workspace-folder . --config .devcontainer/local/devcontainer.json \
    --remote-env SSH_AUTH_SOCK=/ssh-agent git fetch
  ```
  Add `--remove-existing-container` to `up` if the container already exists.
  `--remote-env SSH_AUTH_SOCK=/ssh-agent` must be repeated on every `devcontainer exec` call, or set `SSH_AUTH_SOCK=/ssh-agent` in the container's shell profile (e.g. `~/.bashrc`) to apply it to all sessions.

The `initializeCommand` creates an empty `~/.ssh/known_hosts` on your host if it is missing. To populate it with GitHub's host key, run on your host:
```bash
ssh-keyscan github.com >> ~/.ssh/known_hosts
# Verify fingerprints against GitHub's published list:
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
# e.g. ED25519: SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
```

**First-time auth:** The OAuth token lives in the macOS Keychain, which Docker cannot access. After the first rebuild, open a terminal inside the container and run:

```bash
claude auth login
```

Complete the browser OAuth flow once. With the local config, `~/.claude` is bind-mounted to your host, so the credentials file is written there and persists across future rebuilds — you will not need to log in again. With the default config the login does not survive a rebuild.

#### Running an analysis in the container

```bash
# Analyze the repo (x86_64 only; see arm64 note above)
codeboarding full --local /path/to/repo

# Render HTML from the existing analysis (no re-analysis)
codeboarding-render /path/to/repo/.codeboarding/analysis.json --format html
```

Output is written to `/path/to/repo/.codeboarding/`.

#### Viewing the analysis file

**Web platform (recommended):** The workspace is bind-mounted, so `.codeboarding/analysis.json`
is in your host workspace at the corresponding relative path. Open [app.codeboarding.org](https://app.codeboarding.org),
click **Switch source → File**, and pick the file. In Codespaces, download it first via
VS Code's Explorer → right-click → Download.

**HTML output in the browser:** If you generated `--render html`, serve it from the container
and VS Code port-forwards it automatically:

```bash
python -m http.server 8080 --directory /path/to/repo/.codeboarding
```

Then open the forwarded port 8080 in your browser.

**VS Code extension:** Install the [CodeBoarding extension](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding)
in the container (add `"Codeboarding.codeboarding"` to `customizations.vscode.extensions`
in `.devcontainer/devcontainer.json`) to browse the analysis directly in the editor.

For active contributors who want to modify the code:

```bash
# Install with dev dependencies (includes Black formatter and pre-commit)
uv sync --dev

# Activate virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the language servers and pre-commit hooks
codeboarding-setup
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
| `static_analyzer/config.py` | Add a value to the `Language` enum. Qualified names use the universal `.` delimiter defined in `ClusteringConfig.QUALIFIED_NAME_DELIMITER`. |
| `static_analyzer/__init__.py` | Add a mapping in `_lang_to_adapter_name()` from the `ProgrammingLanguage` name to the adapter registry key. |
| `infra/vscode_constants.py` | Add an LSP server config entry to `VSCODE_CONFIG["lsp_servers"]` with the server name, command, languages, file extensions, and install command. |
| `infra/tool_registry/registry.py` | Add a `ToolDependency` entry to `TOOL_REGISTRY` (see below). |
| `static_analyzer/engine/source_inspector.py` + `pyproject.toml` | Add the language's `tree-sitter-<lang>` grammar: call sites are found in its parse tree and resolved through `textDocument/definition`. Check that its call, member-access and type-declaration node shapes are recognised. A grammar with no current wheel of its own can come from `tree-sitter-language-pack`, as Kotlin's does (`infra/tool_registry/grammars.py`); setup downloads it. |

### 5b) Registering the LSP server dependency

The LSP server must be registered in `infra/tool_registry/registry.py` so it gets installed automatically. The `ToolDependency` entry depends on how the server is distributed:

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

**Release archive with a launcher** (e.g. kotlin-lsp) — a zip or tarball per platform whose launcher is run from inside it:
```python
ToolDependency(
    key="kotlin",
    binary_name="intellij-server",
    kind=ToolKind.ARCHIVE,
    config_section=ConfigSection.LSP_SERVERS,
    source=UpstreamToolSource(
        tag=KOTLIN_LSP_VERSION,
        url_template=KOTLIN_LSP_URL_TEMPLATE,  # with {version} and {asset}
        asset_arch_overrides={("Linux", "x86_64"): "kotlin-server-<version>.tar.gz", ...},
        sha256={"kotlin-server-<version>.tar.gz": "...", ...},
    ),
    archive_subdir="kotlin-lsp",
    archive_entry="bin/intellij-server",
    license_url=KOTLIN_LSP_LICENSE_URL,
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
- Run `uv run pytest` to verify nothing is broken
- Run `uv run mypy .` and `uv run black . --check`

### 5e) Integration tests

The integration suite — which runs real language servers over real projects and
asserts the analysis output — lives in a **private** repository. It stays closed
because its fixtures encode the exact expected output for every supported
language, and we don't want that set copied or tuned against.

What this means for you:

- **You don't need it to contribute.** `uv run pytest` covers everything in this
  repo, and CI runs the private suite against your branch.
- **On PRs from a fork it does not run automatically.** GitHub does not give
  fork PRs access to the credentials needed to reach the private repo, and the
  workarounds would let PR code read the suite. A maintainer runs it against
  your branch before merging; if it fails, they'll tell you which language and
  which metric moved.
- **Adding a language?** Say so in your PR. A maintainer adds the matching
  fixtures — a hand-written edge-case project exercising the language's
  interfaces, generics and inheritance, and a pinned real-world repo with
  expected reference/package/call-graph counts. Tell us which repo you'd pin and
  why, and we'll wire it up.

Note that the integration runners uninstall their system Node first, so the
suite exercises the Node that `codeboarding-setup` bootstraps for itself — the
same one a user without Node gets. That is why install and `tool_registry`
changes trigger it.

---

## 6) How to PR
- Fork the repo.
- Create a branch: `feat/...`, `fix/...`, or `docs/...`.
- In the PR, explain **WHAT/WHY/HOW**: what changed, why it's useful, and how you tested it.
- Optional: add a picture of a diagram from your favorite project.
