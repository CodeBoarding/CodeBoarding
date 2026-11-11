#!/usr/bin/env bash
set -euo pipefail

# Bump to the latest stable @anthropic-ai/claude-code release when refreshing the devcontainer.
# Dependabot cannot track this; a Renovate regex manager could automate it.
CLAUDE_CODE_VERSION="2.1.309"

cd "$(dirname "$0")/.."

# Fix volume ownership (new Docker volumes are root-owned)
sudo chown "$(id -u):$(id -g)" "$HOME/.codeboarding"

# Prevent git's dubious-ownership check from blocking pre-commit install
git config --global --add safe.directory "$PWD"

# Install project and dev dependencies (editable install — source changes are live)
uv sync --dev --locked

# Confirm Python >= 3.12 is in use
if ! /home/vscode/.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "ERROR: expected Python >= 3.12, got $(/home/vscode/.venv/bin/python --version)" >&2
    exit 1
fi

# Install language servers, Node runtime, and pre-commit hooks
if [[ "${CODEBOARDING_DEVCONTAINER_CI:-}" != "1" ]]; then
    # CI sets CODEBOARDING_DEVCONTAINER_CI=1 to skip the ~1 GB language-server download and the Claude CLI.
    uv run codeboarding-setup --auto-install-npm
else
    echo "CODEBOARDING_DEVCONTAINER_CI=1: skipping codeboarding-setup (language servers)"
fi

# Explicitly install pre-commit hook (codeboarding-setup swallows hook install errors)
uv run pre-commit install --install-hooks

# Verify the hook was actually written
hook="$(git rev-parse --git-path hooks/pre-commit)"
if [[ ! -s "$hook" ]] || [[ ! -x "$hook" ]]; then
    echo "ERROR: pre-commit hook was not installed at $hook" >&2
    exit 1
fi

# Warn on arm64: tokei/gopls binaries are x86_64-only
if [ "$(uname -m)" = "aarch64" ] || [ "$(uname -m)" = "arm64" ]; then
    echo "WARNING: arm64 detected. tokei and gopls are x86_64-only, so 'codeboarding full' will not work in this container. Tests, mypy and black are unaffected." >&2
fi

# Install Claude Code CLI so the VS Code extension has a binary to call
if [[ "${CODEBOARDING_DEVCONTAINER_CI:-}" != "1" ]]; then
    npm install -g @anthropic-ai/claude-code@"${CLAUDE_CODE_VERSION}"
else
    echo "CODEBOARDING_DEVCONTAINER_CI=1: skipping Claude Code CLI install"
fi

echo "Dev container ready. Set your LLM provider key in ~/.codeboarding/config.toml (persisted across rebuilds)."
