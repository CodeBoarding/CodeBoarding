"""User-level configuration for CodeBoarding.

Reads ~/.codeboarding/config.toml and exposes typed settings.
The file is optional — all fields default to None (= use provider default / env var).

On startup, call apply_to_env() so that API keys set in config.toml are injected
into os.environ, where the rest of the codebase already reads them from.
Environment variables already set in the shell always take precedence.
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


CONFIG_PATH = Path.home() / ".codeboarding" / "config.toml"

# Mapping from config.toml key names to the env var each provider reads.
# These are the canonical env var names used in agents/llm_config.py.
_PROVIDER_KEY_TO_ENV: dict[str, str] = {
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "vercel_api_key": "VERCEL_API_KEY",
    "aws_bearer_token_bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "cerebras_api_key": "CEREBRAS_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "glm_api_key": "GLM_API_KEY",
    "kimi_api_key": "KIMI_API_KEY",
    "ollama_base_url": "OLLAMA_BASE_URL",
}

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
# anthropic_api_key         = "sk-ant-..."
# google_api_key            = "AIza..."
# vercel_api_key            = "vck_..."
# aws_bearer_token_bedrock  = "..."
# cerebras_api_key          = "..."
# deepseek_api_key          = "..."
# glm_api_key               = "..."
# kimi_api_key              = "..."
# ollama_base_url           = "http://localhost:11434"

# Optional: override the default models chosen by the active provider.
# If omitted, each provider's built-in defaults are used.
[llm]
# agent_model   = "gemini-3-flash"
# parsing_model = "gemini-3-flash"
"""


@dataclass
class ProviderUserConfig:
    """Raw API key / URL values read from [provider] in config.toml."""

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    vercel_api_key: str | None = None
    aws_bearer_token_bedrock: str | None = None
    cerebras_api_key: str | None = None
    deepseek_api_key: str | None = None
    glm_api_key: str | None = None
    kimi_api_key: str | None = None
    ollama_base_url: str | None = None


@dataclass
class LLMUserConfig:
    agent_model: str | None = None
    parsing_model: str | None = None


@dataclass
class UserConfig:
    provider: ProviderUserConfig = field(default_factory=ProviderUserConfig)
    llm: LLMUserConfig = field(default_factory=LLMUserConfig)

    def apply_to_env(self) -> None:
        """Inject config values into os.environ, without overriding existing shell vars."""
        for config_key, env_var in _PROVIDER_KEY_TO_ENV.items():
            value = getattr(self.provider, config_key, None)
            if value and not os.environ.get(env_var):
                os.environ[env_var] = value


def load_user_config(path: Path = CONFIG_PATH) -> UserConfig:
    """Load ~/.codeboarding/config.toml.  Missing file → all defaults."""
    if not path.exists():
        return UserConfig()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    provider_data = data.get("provider", {})
    llm_data = data.get("llm", {})

    return UserConfig(
        provider=ProviderUserConfig(
            openai_api_key=provider_data.get("openai_api_key") or None,
            anthropic_api_key=provider_data.get("anthropic_api_key") or None,
            google_api_key=provider_data.get("google_api_key") or None,
            vercel_api_key=provider_data.get("vercel_api_key") or None,
            aws_bearer_token_bedrock=provider_data.get("aws_bearer_token_bedrock") or None,
            cerebras_api_key=provider_data.get("cerebras_api_key") or None,
            deepseek_api_key=provider_data.get("deepseek_api_key") or None,
            glm_api_key=provider_data.get("glm_api_key") or None,
            kimi_api_key=provider_data.get("kimi_api_key") or None,
            ollama_base_url=provider_data.get("ollama_base_url") or None,
        ),
        llm=LLMUserConfig(
            agent_model=llm_data.get("agent_model") or None,
            parsing_model=llm_data.get("parsing_model") or None,
        ),
    )


def ensure_config_template(path: Path = CONFIG_PATH) -> None:
    """Write the config template if the file does not yet exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE)
