# Dev Container

CodeBoarding ships two dev container configurations.

| Config | Path | Best for |
|---|---|---|
| Default | `.devcontainer/devcontainer.json` | GitHub Codespaces and any environment without host access |
| Local | `.devcontainer/local/devcontainer.json` | Local VS Code with Docker, adds host-volume mounts |

## Post-create script

When the container starts, `.devcontainer/post-create.sh` runs automatically and:

1. Installs project dependencies: `uv sync --dev --locked`
2. Installs pre-commit hooks: `uv run pre-commit install`
3. Installs language servers: `uv run codeboarding-setup --auto-install-npm` (skipped when `CODEBOARDING_DEVCONTAINER_CI=1`)
4. Installs the Claude CLI (skipped when `CODEBOARDING_DEVCONTAINER_CI=1`)

Project files are installed in editable mode, so `codeboarding` commands always use the latest source.

## Local config — host mounts

The local config (`.devcontainer/local/devcontainer.json`, VS Code name **CodeBoarding (local, host mounts)**) adds mounts so container tools can reach host resources:

| Mount | Target | Purpose |
|---|---|---|
| Named volume `codeboarding-home-${devcontainerId}` | `/home/vscode/.codeboarding` | Analysis output and `config.toml` (persists across rebuilds) |
| `~/.claude` (bind) | `/home/vscode/.claude` | Claude Code settings, memory, skills, and credentials (read-write) |
| `~/.ssh/known_hosts` (bind, read-only) | `/etc/ssh/ssh_known_hosts` | SSH host keys — no private keys |

`~/.claude` is mounted writable on purpose, because Claude Code writes settings, memory and login credentials back to the host. `known_hosts` is mounted `readonly` because the container never needs to change it.

To remove the named volume, find it with `docker volume ls | grep codeboarding-` and delete it with `docker volume rm <name>`.

Telemetry is off (`DO_NOT_TRACK=1` is set in `containerEnv`).

**Note:** `.devcontainer/local/devcontainer-lock.json` is a copy of `.devcontainer/devcontainer-lock.json` — update both when bumping feature versions.

## Claude Code authentication

The Claude Code extension is installed automatically. With the local config, `~/.claude` is bind-mounted from the host, so credentials written inside the container persist across rebuilds.

On first start (or in the default config), authenticate inside the container:

```bash
claude auth login
```

Complete the browser OAuth flow once. With the default config the login does not survive a rebuild.

## SSH access

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

The `initializeCommand` creates an empty `~/.ssh/known_hosts` on your host if it is missing. To populate it with GitHub's host key, run on your host:

```bash
ssh-keyscan github.com >> ~/.ssh/known_hosts
```

## Running an analysis

> **ARM64 note:** full analysis is unavailable on ARM64 (language servers are x86_64-only). Tests and development checks work normally.

For common commands, see the main [README](../README.md#common-commands).

## Viewing results in a browser

Both configs declare `"forwardPorts": [8080]`, but nothing listens on that port until you start a server inside the container:

```bash
python -m http.server 8080 --directory /path/to/repo/.codeboarding
```

VS Code then forwards port 8080; open it in your browser.

You can also upload `.codeboarding/analysis.json` to [app.codeboarding.org](https://app.codeboarding.org), or install the [CodeBoarding VS Code extension](https://marketplace.visualstudio.com/items?itemName=Codeboarding.codeboarding) to browse the analysis directly in the editor.

## CI

CI (`.github/workflows/devcontainer.yml`) builds the container when the devcontainer config or the files its tests depend on change (see the workflow's `paths:` filter), with `CODEBOARDING_DEVCONTAINER_CI=1` set to skip the language-server download and the Claude CLI install.
