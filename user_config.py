"""User-level configuration for CodeBoarding.

Reads ~/.codeboarding/config.toml and exposes typed settings.
The file is optional — all fields default to None (= use provider default / env var).

On startup, call apply_to_env() so that API keys set in config.toml are injected
into os.environ, where the rest of the codebase already reads them from.
Environment variables already set in the shell always take precedence.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from utils import CODEBOARDING_DIR_NAME

CONFIG_PATH = Path.home() / CODEBOARDING_DIR_NAME / "config.toml"

# These mappings feed config.toml's [provider] table; values are injected into
# os.environ at startup. Provider selection itself is decided solely by
# LLMConfig.selection_envs in agents/llm_config.py — membership here only
# controls what can live in config.toml (contract-tested in tests/test_user_config.py).

# Secrets: one per provider that accepts a key (api_key_env in agents/llm_config.py).
_PROVIDER_SECRETS: dict[str, str] = {
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "vercel_api_key": "VERCEL_API_KEY",
    "aws_bearer_token_bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "cerebras_api_key": "CEREBRAS_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "glm_api_key": "GLM_API_KEY",
    "kimi_api_key": "KIMI_API_KEY",
    "ollama_api_key": "OLLAMA_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "orcarouter_api_key": "ORCAROUTER_API_KEY",
    "litellm_api_key": "LITELLM_API_KEY",
}

# Endpoints with no built-in default — the user must say where the server is.
# Defaulted base URLs (vercel, deepseek, glm, kimi) are shell-override-only on purpose.
_PROVIDER_ENDPOINTS: dict[str, str] = {
    "openai_base_url": "OPENAI_BASE_URL",
    "ollama_base_url": "OLLAMA_BASE_URL",
    "litellm_base_url": "LITELLM_BASE_URL",
}

# Optional endpoints forwarded after another setting selects the provider.
_PROVIDER_ENDPOINT_OVERRIDES: dict[str, str] = {
    "anthropic_base_url": "ANTHROPIC_BASE_URL",
}

_PROVIDER_KEY_TO_ENV: dict[str, str] = _PROVIDER_SECRETS | _PROVIDER_ENDPOINTS | _PROVIDER_ENDPOINT_OVERRIDES

GROUPER_ENV = "CODEBOARDING_GROUPER"
GROUPERS = ("affinity", "kinship", "planner")
"""How frontier candidates become components: ``affinity`` merges scopes sharing a word, then
folds a scope toward nine components along the call graph, with no model; ``kinship`` stops
after the shared words; ``planner`` lets an LLM group across words toward a 5-9 preference."""

# Template written to ~/.codeboarding/config.toml on first install.
CONFIG_TEMPLATE = """\
# CodeBoarding user configuration
# Location: ~/.codeboarding/config.toml
#
# Uncomment and fill in exactly ONE provider key below.
# Keys set here are injected into environment variables at startup.
# Shell environment variables always take precedence over this file.

[provider]
# openai_api_key            = "sk-..."
# openai_base_url           = "http://localhost:8000/v1"   # self-hosted / OpenAI-compatible proxy
# anthropic_api_key         = "sk-ant-..."
# anthropic_base_url        = "https://resource.services.ai.azure.com/anthropic"
# google_api_key            = "AIza..."
# vercel_api_key            = "vck_..."
# aws_bearer_token_bedrock  = "..."
# cerebras_api_key          = "..."
# deepseek_api_key          = "..."
# glm_api_key               = "..."
# kimi_api_key              = "..."
# ollama_base_url           = "http://localhost:11434"
# ollama_api_key            = "..."              # only for Ollama cloud (https://ollama.com)
# openrouter_api_key        = "sk..."
# orcarouter_api_key        = "sk-orca-..."
# litellm_base_url          = "http://localhost:4000"  # LiteLLM proxy server URL (required)
# litellm_api_key           = "sk-..."           # LiteLLM proxy server key (optional)

# Optional: override the default models chosen by the active provider.
# If omitted, each provider's built-in defaults are used.
[llm]
# agent_model    = "google/gemini-3.8-flash"
# context_window = 272000   # override if needed

# Optional: how top-level components are grouped from the repository's names.
# "affinity" (default) merges scopes that share a word, then folds toward 9 components
# along the call graph, with no model call; "kinship" stops after the shared words;
# "planner" asks the LLM to group across words toward 5-9 components.
[clustering]
# grouper = "affinity"
"""


@dataclass
class ProviderUserConfig:
    """Raw API key / URL values read from [provider] in config.toml."""

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    google_api_key: str | None = None
    vercel_api_key: str | None = None
    aws_bearer_token_bedrock: str | None = None
    cerebras_api_key: str | None = None
    deepseek_api_key: str | None = None
    glm_api_key: str | None = None
    kimi_api_key: str | None = None
    ollama_base_url: str | None = None
    ollama_api_key: str | None = None
    openrouter_api_key: str | None = None
    orcarouter_api_key: str | None = None
    litellm_api_key: str | None = None
    litellm_base_url: str | None = None


@dataclass
class LLMUserConfig:
    agent_model: str | None = None
    context_window: int | None = None


@dataclass
class ClusteringUserConfig:
    grouper: str | None = None


@dataclass
class UserConfig:
    provider: ProviderUserConfig = field(default_factory=ProviderUserConfig)
    llm: LLMUserConfig = field(default_factory=LLMUserConfig)
    clustering: ClusteringUserConfig = field(default_factory=ClusteringUserConfig)

    def apply_to_env(self) -> None:
        """Inject config values into os.environ, without overriding existing shell vars."""
        for config_key, env_var in _PROVIDER_KEY_TO_ENV.items():
            value = getattr(self.provider, config_key, None)
            if value and not os.environ.get(env_var):
                os.environ[env_var] = value
        if self.clustering.grouper and not os.environ.get(GROUPER_ENV):
            os.environ[GROUPER_ENV] = self.clustering.grouper


def load_user_config(path: Path = CONFIG_PATH) -> UserConfig:
    """Load ~/.codeboarding/config.toml.  Missing file -> all defaults."""
    if not path.exists():
        return UserConfig()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    provider_data = data.get("provider", {})
    llm_data = data.get("llm", {})
    clustering_data = data.get("clustering", {})
    grouper = clustering_data.get("grouper") or None
    if grouper is not None and grouper not in GROUPERS:
        raise ValueError(f"[clustering] grouper must be one of {', '.join(GROUPERS)}, not {grouper!r}")

    return UserConfig(
        provider=ProviderUserConfig(
            openai_api_key=provider_data.get("openai_api_key") or None,
            openai_base_url=provider_data.get("openai_base_url") or None,
            anthropic_api_key=provider_data.get("anthropic_api_key") or None,
            anthropic_base_url=provider_data.get("anthropic_base_url") or None,
            google_api_key=provider_data.get("google_api_key") or None,
            vercel_api_key=provider_data.get("vercel_api_key") or None,
            aws_bearer_token_bedrock=provider_data.get("aws_bearer_token_bedrock") or None,
            cerebras_api_key=provider_data.get("cerebras_api_key") or None,
            deepseek_api_key=provider_data.get("deepseek_api_key") or None,
            glm_api_key=provider_data.get("glm_api_key") or None,
            kimi_api_key=provider_data.get("kimi_api_key") or None,
            ollama_base_url=provider_data.get("ollama_base_url") or None,
            ollama_api_key=provider_data.get("ollama_api_key") or None,
            openrouter_api_key=provider_data.get("openrouter_api_key") or None,
            orcarouter_api_key=provider_data.get("orcarouter_api_key") or None,
            litellm_api_key=provider_data.get("litellm_api_key") or None,
            litellm_base_url=provider_data.get("litellm_base_url") or None,
        ),
        llm=LLMUserConfig(
            agent_model=llm_data.get("agent_model") or None,
            context_window=llm_data.get("context_window"),
        ),
        clustering=ClusteringUserConfig(grouper=grouper),
    )


def ensure_config_template(path: Path = CONFIG_PATH) -> None:
    """Write the template on first install; otherwise top up with any keys added since."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_TEMPLATE)
        return
    _append_commented_key(path, "context_window", "# context_window = 272000   # override if needed")


def _append_commented_key(path: Path, key: str, commented_line: str) -> None:
    """Insert `commented_line` under [llm] if `key` is missing anywhere in the file."""
    text = path.read_text()
    if key in text:
        return
    injected, n = re.subn(r"(^\[llm\]\s*\n)", r"\1" + commented_line + "\n", text, count=1, flags=re.MULTILINE)
    if n == 0:
        # Why: no [llm] section yet -- append a fresh one so the key lands in the right table.
        injected = text.rstrip() + "\n\n[llm]\n" + commented_line + "\n"
    path.write_text(injected)
