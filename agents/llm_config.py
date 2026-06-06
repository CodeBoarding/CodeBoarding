import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Type

from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_cerebras import ChatCerebras
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from agents.constants import LLMDefaults, ModelCapabilities
from agents.model_capabilities import ContextWindow, get_context_window
from agents.opencode_chat import ChatOpenCode
from agents.opencode_launcher import OpenCodeLauncher
from agents.prompts.prompt_factory import LLMType, initialize_global_factory
from monitoring.callbacks import MonitoringCallback

# Initialize global monitoring callback with its own stats container to avoid ContextVar dependency
from monitoring.stats import RunStats

MONITORING_CALLBACK = MonitoringCallback(stats_container=RunStats())

# Global OpenCode launcher instance (managed lifecycle)
_opencode_launcher: OpenCodeLauncher | None = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level model overrides – set once by the orchestrator (main.py) and
# consumed by initialize_llms() without needing to thread the values through
# every intermediate function signature.
# ---------------------------------------------------------------------------
_agent_model_override: str | None = None
_parsing_model_override: str | None = None


def configure_models(
    agent_model: str | None = None,
    parsing_model: str | None = None,
    api_keys: dict[str, str] | None = None,
) -> None:
    """Set process-wide model and provider overrides.  Call this once at startup.

    ``api_keys`` maps provider env-var names to values, e.g.::

        configure_models(api_keys={"OPENAI_API_KEY": "sk-..."})

    Keys already present in the shell environment are never overwritten, so
    CI/CD pipelines that export keys directly retain full control.

    Priority (highest to lowest):
      1. Shell environment variables (set before the process starts)
      2. ``api_keys`` passed here  /  values from ~/.codeboarding/config.toml
      3. AGENT_MODEL / PARSING_MODEL environment variables (for model names)
      4. Provider defaults defined in LLM_PROVIDERS
    """
    global _agent_model_override, _parsing_model_override
    _agent_model_override = agent_model
    _parsing_model_override = parsing_model
    if api_keys:
        for env_var, value in api_keys.items():
            if value and not os.environ.get(env_var):
                os.environ[env_var] = value


@dataclass
class LLMConfig:
    """
    Configuration for LLM providers.

    Attributes:
        agent_model: The "agent" model used for complex reasoning and agentic tasks.
        parsing_model: The "parsing" model used for fast, cost-effective extraction and parsing tasks.
        agent_temperature: Temperature for the agent model. Defaults to 0 for deterministic behavior
                          which is crucial for code understanding and reasoning.
        parsing_temperature: Temperature for the parsing model. Defaults to 0 for deterministic behavior
                          which is crucial for structured output extraction.
        llm_type: The LLMType enum value for prompt factory selection.
    """

    chat_class: Type[BaseChatModel]
    api_key_env: str
    agent_model: str
    parsing_model: str
    llm_type: LLMType
    agent_temperature: float = LLMDefaults.DEFAULT_AGENT_TEMPERATURE
    parsing_temperature: float = LLMDefaults.DEFAULT_PARSING_TEMPERATURE
    extra_args: dict[str, Any] = field(default_factory=dict)
    alt_env_vars: list[str] = field(default_factory=list)
    keyless_capable: bool = False
    """Whether this provider can run without a real API key.

    True for self-hosted / OpenAI-compatible endpoints where ``api_key_env`` (or
    an ``alt_env_vars`` entry) is really a base-URL existence check rather than a
    secret. When such a provider is the sole active one and no real key is set,
    key validation warns instead of failing, and the client uses a placeholder.
    """

    def get_api_key(self) -> str | None:
        return os.getenv(self.api_key_env)

    def has_real_api_key(self) -> bool:
        """True if the provider's primary API-key env var holds a value.

        Distinct from ``is_active()``: a keyless-capable provider can be active
        via a base-URL ``alt_env_vars`` entry while having no real key here.
        """
        return bool(os.getenv(self.api_key_env))

    def is_active(self) -> bool:
        """Check if any of the environment variables (primary or alternate) are set."""
        if os.getenv(self.api_key_env):
            return True
        return any(os.getenv(var) for var in self.alt_env_vars)

    def get_resolved_extra_args(self) -> dict[str, Any]:
        resolved = {}
        for k, v in self.extra_args.items():
            value = v() if callable(v) else v
            if value is not None:
                resolved[k] = value
        return resolved


# Define supported providers in priority order
LLM_PROVIDERS = {
    "openai": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="OPENAI_API_KEY",
        agent_model="gpt-4o",
        parsing_model="gpt-4o-mini",
        llm_type=LLMType.GPT4,
        alt_env_vars=["OPENAI_BASE_URL"],
        keyless_capable=True,
        extra_args={
            "base_url": lambda: os.getenv("OPENAI_BASE_URL"),
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "vercel": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="VERCEL_API_KEY",
        agent_model="google/gemini-3-flash",
        parsing_model="openai/gpt-5-mini",
        llm_type=LLMType.GEMINI_FLASH,
        alt_env_vars=["VERCEL_BASE_URL"],
        extra_args={
            "base_url": lambda: os.getenv("VERCEL_BASE_URL", f"https://ai-gateway.vercel.sh/v1"),
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "anthropic": LLMConfig(
        chat_class=ChatAnthropic,
        api_key_env="ANTHROPIC_API_KEY",
        agent_model="claude-sonnet-4-6",
        parsing_model="claude-haiku-4-5",
        llm_type=LLMType.CLAUDE,
        extra_args={
            "max_tokens": 8192,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "google": LLMConfig(
        chat_class=ChatGoogleGenerativeAI,
        api_key_env="GOOGLE_API_KEY",
        agent_model="gemini-3-flash-preview",
        parsing_model="gemini-3.1-flash-lite",
        llm_type=LLMType.GEMINI_FLASH,
        extra_args={
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "aws": LLMConfig(
        chat_class=ChatBedrockConverse,
        api_key_env="AWS_BEARER_TOKEN_BEDROCK",  # Used for existence check
        agent_model="anthropic.claude-sonnet-4-6",
        parsing_model="claude-haiku-4-5",
        llm_type=LLMType.CLAUDE_SONNET,
        extra_args={
            "max_tokens": 4096,
            "region_name": lambda: os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            "credentials_profile_name": None,
        },
    ),
    "cerebras": LLMConfig(
        chat_class=ChatCerebras,
        api_key_env="CEREBRAS_API_KEY",
        agent_model="zai-glm-4.7",
        parsing_model="gpt-oss-120b",
        llm_type=LLMType.KIMI,
        extra_args={
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "ollama": LLMConfig(
        chat_class=ChatOllama,
        api_key_env="OLLAMA_BASE_URL",  # Used for existence check
        agent_model="qwen3:30b",
        parsing_model="qwen2.5:7b",
        llm_type=LLMType.GEMINI_FLASH,
        agent_temperature=LLMDefaults.DEFAULT_AGENT_TEMPERATURE,
        parsing_temperature=LLMDefaults.DEFAULT_PARSING_TEMPERATURE,
        extra_args={
            "base_url": lambda: os.getenv("OLLAMA_BASE_URL"),
        },
    ),
    "deepseek": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="DEEPSEEK_API_KEY",
        agent_model="deepseek-chat",
        parsing_model="deepseek-chat",
        llm_type=LLMType.DEEPSEEK,
        alt_env_vars=["DEEPSEEK_BASE_URL"],
        extra_args={
            "base_url": lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "glm": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="GLM_API_KEY",
        agent_model="glm-4.7-flash",
        parsing_model="glm-4.7-flash",
        llm_type=LLMType.GLM,
        alt_env_vars=["GLM_BASE_URL"],
        extra_args={
            "base_url": lambda: os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "kimi": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="KIMI_API_KEY",
        agent_model="kimi-k2.5",
        parsing_model="kimi-k2.5",
        llm_type=LLMType.KIMI,
        alt_env_vars=["KIMI_BASE_URL"],
        extra_args={
            "base_url": lambda: os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "openrouter": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="OPENROUTER_API_KEY",
        agent_model="google/gemini-3-flash-preview",
        parsing_model="google/gemini-3-flash-preview",
        llm_type=LLMType.GEMINI_FLASH,
        extra_args={
            "base_url": lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
    "opencode": LLMConfig(
        chat_class=ChatOpenCode,
        api_key_env="OPENCODE_BASE_URL",
        agent_model="opencode-go/qwen3.6-plus",
        parsing_model="opencode-go/qwen3.6-plus",
        llm_type=LLMType.OPENCODE,
        alt_env_vars=["OPENCODE_SERVER_PASSWORD"],
        extra_args={
            "base_url": lambda: os.getenv("OPENCODE_BASE_URL", "http://localhost:4096"),
            "password": lambda: os.getenv("OPENCODE_SERVER_PASSWORD"),
            "max_tokens": None,
            "timeout": 120,
        },
    ),
}


def configure_opencode_launcher(repo_dir: Path) -> None:
    """Configure the OpenCode launcher for the given repository.

    Call this before initialize_llms() when using the OpenCode provider.
    The launcher will be started automatically during LLM initialization.
    """
    global _opencode_launcher
    _opencode_launcher = OpenCodeLauncher(repo_dir=repo_dir)


def get_opencode_launcher() -> OpenCodeLauncher | None:
    """Get the configured OpenCode launcher instance."""
    return _opencode_launcher


def cleanup_opencode_launcher() -> None:
    """Stop and clean up the OpenCode launcher."""
    global _opencode_launcher
    if _opencode_launcher is not None:
        _opencode_launcher.stop()
        _opencode_launcher = None


def _initialize_llm(
    model_override: str | None,
    model_attr: str,
    temperature_attr: str,
    log_prefix: str,
    init_factory: bool = False,
) -> tuple[BaseChatModel, str]:
    resolved = _resolve_active_provider(model_override, model_attr)
    if resolved is None:
        required_vars = []
        for config in LLM_PROVIDERS.values():
            required_vars.append(config.api_key_env)
            required_vars.extend(config.alt_env_vars)

        raise ValueError(
            f"No valid LLM configuration found. Please set one of: {', '.join(sorted(set(required_vars)))}"
        )

    name, config, model_name = resolved

    if init_factory:
        detected_llm_type = LLMType.from_model_name(model_name)
        initialize_global_factory(detected_llm_type)
        logger.info(
            f"Initialized prompt factory for {name} provider with model '{model_name}' "
            f"-> {detected_llm_type.value} prompt factory"
        )

    logger.info(f"Using {name.title()} {log_prefix}LLM with model: {model_name}")

    kwargs = {
        "model": model_name,
        "temperature": getattr(config, temperature_attr),
    }
    kwargs.update(config.get_resolved_extra_args())

    if name == "opencode":
        global _opencode_launcher
        user_base_url = os.getenv("OPENCODE_BASE_URL")
        if _opencode_launcher is not None and not user_base_url:
            if not _opencode_launcher.is_running:
                _opencode_launcher.start()
            kwargs["base_url"] = _opencode_launcher.base_url
        else:
            kwargs["base_url"] = kwargs.get("base_url", "http://localhost:4096")
        if "password" in kwargs and kwargs["password"] is None:
            kwargs.pop("password")
    elif name not in ["aws", "ollama"]:
        api_key = config.get_api_key()
        kwargs["api_key"] = api_key or "no-key-required"

    model = config.chat_class(**kwargs)  # type: ignore[call-arg, arg-type]
    return model, model_name


def _resolve_active_provider(
    model_override: str | None,
    model_attr: str,
) -> tuple[str, LLMConfig, str] | None:
    """Return the active provider, config, and resolved model name."""
    for name, config in LLM_PROVIDERS.items():
        if not config.is_active():
            continue
        return name, config, model_override or getattr(config, model_attr)
    return None


class LLMConfigError(ValueError):
    """Raised when LLM provider keys are missing or ambiguous."""


def validate_api_key_provided() -> None:
    """Raise LLMConfigError if zero or more than one LLM provider is configured.

    A provider is "active" when its API-key env var *or* a base-URL alternate
    (``alt_env_vars``) is set. Keyless-capable providers (self-hosted /
    OpenAI-compatible endpoints, e.g. an ``OPENAI_BASE_URL`` with no key) are
    therefore valid: they are activated by their base URL, and the client falls
    back to a placeholder key downstream. In that case we log a warning rather
    than fail. Ambiguity detection (more than one active provider) is preserved
    so a stray second key is still surfaced.
    """
    active = [name for name, config in LLM_PROVIDERS.items() if config.is_active()]
    if not active:
        required = sorted({config.api_key_env for config in LLM_PROVIDERS.values()})
        raise LLMConfigError(f"No LLM provider API key found. Set one of: {', '.join(required)}")
    if len(active) > 1:
        raise LLMConfigError(f"Multiple LLM provider keys detected ({', '.join(active)}); please set only one.")

    (name,) = active
    config = LLM_PROVIDERS[name]
    if config.keyless_capable and not config.has_real_api_key():
        logger.warning(
            "Provider '%s' is active via a base URL with no %s set; "
            "treating as a keyless local endpoint (a placeholder key is used).",
            name,
            config.api_key_env,
        )


def initialize_agent_llm(model_override: str | None = None) -> BaseChatModel:
    model, model_name = _initialize_llm(model_override, "agent_model", "agent_temperature", "", init_factory=True)
    MONITORING_CALLBACK.model_name = model_name
    return model


def get_current_agent_context_window() -> ContextWindow:
    """Context window for the currently active agent provider/model.

    Resolves the first active provider (same rule as ``_initialize_llm``) on
    every call. ``get_context_window`` handles its own caching, so this is
    cheap enough to call without a module-level cache.
    """
    resolved = _resolve_active_provider(_agent_model_override or os.getenv("AGENT_MODEL"), "agent_model")
    if resolved is not None:
        name, _config, model_name = resolved
        return get_context_window(name, model_name)
    return ContextWindow(ModelCapabilities.FALLBACK_INPUT, ModelCapabilities.FALLBACK_OUTPUT)


def initialize_parsing_llm(model_override: str | None = None) -> BaseChatModel:
    model, _ = _initialize_llm(model_override, "parsing_model", "parsing_temperature", "Extractor ")
    return model


def initialize_llms() -> tuple[BaseChatModel, BaseChatModel]:
    agent_llm = initialize_agent_llm(_agent_model_override or os.getenv("AGENT_MODEL"))
    parsing_llm = initialize_parsing_llm(_parsing_model_override or os.getenv("PARSING_MODEL"))
    return agent_llm, parsing_llm


def supports_prompt_caching(llm: BaseChatModel) -> bool:
    """Return True when *llm* supports ephemeral prompt caching.

    Only Anthropic's langchain integration exposes ``cache_control`` blocks
    today; other providers either cache transparently or not at all.
    """
    return llm.__class__.__module__.startswith("langchain_anthropic")
