import logging
import os
from dataclasses import dataclass, field
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_cerebras import ChatCerebras
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from agents.constants import LLMDefaults, ModelCapabilities
from agents.model_capabilities import ContextWindow, get_context_window
from agents.prompts import PromptProfile, resolve_prompt_profile
from monitoring.callbacks import MonitoringCallback

# Initialize global monitoring callback with its own stats container to avoid ContextVar dependency
from monitoring.stats import RunStats

MONITORING_CALLBACK = MonitoringCallback(stats_container=RunStats())

logger = logging.getLogger(__name__)

_OPENROUTER_FALLBACK_CONTEXT_WINDOW = ContextWindow(1_048_576, 65_536, is_fallback=True)

# Model families that must use their provider's default temperature.
# Why: newer Anthropic models reject it, while Gemini 3 degrades below its default of 1.
# Substrings match bare IDs and provider-prefixed forms.
_SAMPLING_PARAM_FREE_MODEL_SUBSTRINGS = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
    "gemini-3",
)


def _model_accepts_temperature(model_name: str) -> bool:
    """Whether the model accepts an explicit temperature."""
    lowered = model_name.lower()
    return not any(s in lowered for s in _SAMPLING_PARAM_FREE_MODEL_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Module-level overrides set once by the orchestrator without threading them
# through every intermediate function signature.
# ---------------------------------------------------------------------------
_agent_model_override: str | None = None


def configure_models(
    agent_model: str | None = None,
    api_keys: dict[str, str] | None = None,
) -> None:
    """Set process-wide model and provider overrides.  Call this once at startup.

    ``api_keys`` maps provider env-var names to values, e.g.::

        configure_models(api_keys={"OPENAI_API_KEY": "sk-..."})

    Keys already present in the shell environment are never overwritten, so
    CI/CD pipelines that export keys directly retain full control.

    Existing shell environment variables take precedence over values loaded
    from ``~/.codeboarding/config.toml``. Explicit ``model_override`` arguments
    to ``initialize_agent_llm`` remain the highest-priority library API.
    """
    global _agent_model_override
    _agent_model_override = agent_model
    if api_keys:
        for env_var, value in api_keys.items():
            if value and not os.environ.get(env_var):
                os.environ[env_var] = value


@dataclass
class LLMConfig:
    """
    Configuration for LLM providers.

    Attributes:
        selection_envs: Env vars that select this provider — any one being set selects it.
        api_key_env: Env var holding the provider's secret, or None when the
                     underlying SDK reads its credentials from the environment itself.
        base_url_env: Env var that overrides the provider endpoint, or an empty string when unsupported.
        default_base_url: Built-in endpoint used when base_url_env is unset, or an empty string for the SDK default.
        agent_model: The "agent" model used for complex reasoning and agentic tasks.
        agent_temperature: Temperature for the agent model. Defaults to 0 for deterministic behavior
                          which is crucial for code understanding and reasoning.
    """

    chat_class: type[BaseChatModel]
    selection_envs: list[str]
    agent_model: str
    agent_temperature: float = LLMDefaults.DEFAULT_AGENT_TEMPERATURE
    extra_args: dict[str, Any] = field(default_factory=dict)
    api_key_env: str | None = None
    base_url_env: str = ""
    default_base_url: str = ""
    keyless_capable: bool = False
    """Whether this provider can run without a real API key.

    True for self-hosted / OpenAI-compatible endpoints that accept
    unauthenticated requests. When such a provider is the sole selected one
    and no real key is set, key validation warns instead of failing, and the
    client uses a placeholder.
    """

    def get_api_key(self) -> str | None:
        return os.getenv(self.api_key_env) if self.api_key_env else None

    def has_real_api_key(self) -> bool:
        """True if the provider's API-key env var holds a value.

        Distinct from ``is_selected_by_env()``: a keyless-capable provider can
        be selected via a base-URL var while having no real key here.
        """
        return bool(self.get_api_key())

    def is_selected_by_env(self) -> bool:
        """True when any of this provider's selection env vars is set."""
        return any(os.getenv(var) for var in self.selection_envs)

    def get_resolved_extra_args(self) -> dict[str, Any]:
        resolved = {}
        for k, v in self.extra_args.items():
            value = v() if callable(v) else v
            if value is not None:
                resolved[k] = value
        if self.base_url_env:
            base_url = os.getenv(self.base_url_env, self.default_base_url or None)
            if base_url is not None:
                resolved["base_url"] = base_url
        return resolved


# Define supported providers in priority order
LLM_PROVIDERS = {
    "openai": LLMConfig(
        chat_class=ChatOpenAI,
        selection_envs=["OPENAI_API_KEY", "OPENAI_BASE_URL"],
        api_key_env="OPENAI_API_KEY",
        agent_model="gpt-4o",
        keyless_capable=True,
        base_url_env="OPENAI_BASE_URL",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "vercel": LLMConfig(
        chat_class=ChatOpenAI,
        selection_envs=["VERCEL_API_KEY", "VERCEL_BASE_URL"],
        api_key_env="VERCEL_API_KEY",
        agent_model="google/gemini-3.8-flash",
        base_url_env="VERCEL_BASE_URL",
        default_base_url="https://ai-gateway.vercel.sh/v1",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "anthropic": LLMConfig(
        chat_class=ChatAnthropic,
        selection_envs=["ANTHROPIC_API_KEY"],
        api_key_env="ANTHROPIC_API_KEY",
        agent_model="claude-sonnet-5",
        base_url_env="ANTHROPIC_BASE_URL",
        extra_args={
            "thinking": {"type": "disabled"},
            "max_tokens": 8192,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "google": LLMConfig(
        chat_class=ChatGoogleGenerativeAI,
        selection_envs=["GOOGLE_API_KEY"],
        api_key_env="GOOGLE_API_KEY",
        agent_model="gemini-3.8-flash",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "aws": LLMConfig(
        chat_class=ChatBedrockConverse,
        # No api_key_env: botocore reads AWS_BEARER_TOKEN_BEDROCK from the environment itself.
        selection_envs=["AWS_BEARER_TOKEN_BEDROCK"],
        agent_model="anthropic.claude-sonnet-4-6",
        extra_args={
            "max_tokens": 4096,
            "region_name": lambda: os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            "credentials_profile_name": None,
        },
    ),
    "cerebras": LLMConfig(
        chat_class=ChatCerebras,
        selection_envs=["CEREBRAS_API_KEY"],
        api_key_env="CEREBRAS_API_KEY",
        agent_model="zai-glm-4.7",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "ollama": LLMConfig(
        chat_class=ChatOllama,
        # OLLAMA_HOST is Ollama's canonical host var; the client falls back to it
        # when no base_url is passed, and sends OLLAMA_API_KEY (Ollama cloud) itself.
        selection_envs=["OLLAMA_BASE_URL", "OLLAMA_HOST"],
        api_key_env="OLLAMA_API_KEY",
        keyless_capable=True,
        agent_model="qwen3:30b",
        agent_temperature=LLMDefaults.DEFAULT_AGENT_TEMPERATURE,
        base_url_env="OLLAMA_BASE_URL",
    ),
    "deepseek": LLMConfig(
        chat_class=ChatOpenAI,
        selection_envs=["DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"],
        api_key_env="DEEPSEEK_API_KEY",
        agent_model="deepseek-v4-flash",
        base_url_env="DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com/v1",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "glm": LLMConfig(
        chat_class=ChatOpenAI,
        selection_envs=["GLM_API_KEY", "GLM_BASE_URL"],
        api_key_env="GLM_API_KEY",
        agent_model="glm-4.7-flash",
        base_url_env="GLM_BASE_URL",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "kimi": LLMConfig(
        chat_class=ChatOpenAI,
        selection_envs=["KIMI_API_KEY", "KIMI_BASE_URL"],
        api_key_env="KIMI_API_KEY",
        agent_model="kimi-k2.6",
        base_url_env="KIMI_BASE_URL",
        default_base_url="https://api.moonshot.cn/v1",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "openrouter": LLMConfig(
        chat_class=ChatOpenAI,
        selection_envs=["OPENROUTER_API_KEY"],
        api_key_env="OPENROUTER_API_KEY",
        agent_model="google/gemini-3.8-flash",
        base_url_env="OPENROUTER_BASE_URL",
        default_base_url="https://openrouter.ai/api/v1",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "orcarouter": LLMConfig(
        chat_class=ChatOpenAI,
        selection_envs=["ORCAROUTER_API_KEY"],
        api_key_env="ORCAROUTER_API_KEY",
        agent_model="openai/gpt-5.4-mini",
        base_url_env="ORCAROUTER_BASE_URL",
        default_base_url="https://api.orcarouter.ai/v1",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
    "litellm": LLMConfig(
        chat_class=ChatOpenAI,
        # Base URL only: a key alone must not select litellm, since there is no
        # universal proxy endpoint to default to.
        selection_envs=["LITELLM_BASE_URL"],
        api_key_env="LITELLM_API_KEY",
        agent_model="gpt-4o",
        keyless_capable=True,
        base_url_env="LITELLM_BASE_URL",
        extra_args={
            "max_tokens": None,
            "timeout": LLMDefaults.REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
    ),
}


LLM_PROVIDER_ENV_VARS: frozenset[str] = frozenset(
    var
    for config in LLM_PROVIDERS.values()
    for var in [*config.selection_envs, config.api_key_env, config.base_url_env]
    if var
)
LLM_ENV_VARS: frozenset[str] = LLM_PROVIDER_ENV_VARS | frozenset({"AGENT_MODEL"})


def _all_selection_envs() -> list[str]:
    return sorted({var for config in LLM_PROVIDERS.values() for var in config.selection_envs})


def _unselected_key_hints() -> list[str]:
    """Messages for providers whose API key is set but which nothing selects."""
    return [
        f"{config.api_key_env} is set, but the '{name}' provider is selected by "
        f"{' or '.join(config.selection_envs)}."
        for name, config in LLM_PROVIDERS.items()
        if config.has_real_api_key() and not config.is_selected_by_env()
    ]


def selected_providers() -> list[str]:
    """Names of providers the environment currently selects."""
    return [name for name, config in LLM_PROVIDERS.items() if config.is_selected_by_env()]


def _initialize_llm(
    model_override: str | None,
) -> tuple[BaseChatModel, str]:
    resolved = _resolve_selected_provider(model_override)
    if resolved is None:
        message = f"No valid LLM configuration found. Please set one of: {', '.join(_all_selection_envs())}."
        raise ValueError(" ".join([message, *_unselected_key_hints()]))

    name, config, model_name = resolved

    logger.info(f"Using {name.title()} LLM with model: {model_name}")

    kwargs: dict[str, Any] = {"model": model_name}
    if _model_accepts_temperature(model_name):
        kwargs["temperature"] = config.agent_temperature
    kwargs.update(config.get_resolved_extra_args())

    # ChatBedrockConverse and ChatOllama take no api_key kwarg; their SDKs read
    # AWS_BEARER_TOKEN_BEDROCK / OLLAMA_API_KEY from the environment directly.
    if name not in ["aws", "ollama"]:
        api_key = config.get_api_key()
        kwargs["api_key"] = api_key or "no-key-required"

    model = config.chat_class(**kwargs)  # type: ignore[call-arg, arg-type]
    return model, model_name


def _resolve_selected_provider(
    model_override: str | None,
) -> tuple[str, LLMConfig, str] | None:
    """Return the selected provider, config, and resolved model name."""
    for name, config in LLM_PROVIDERS.items():
        if not config.is_selected_by_env():
            continue
        return name, config, model_override or config.agent_model
    return None


class LLMConfigError(ValueError):
    """Raised when LLM provider keys are missing or ambiguous."""


def validate_api_key_provided() -> None:
    """Raise LLMConfigError unless exactly one LLM provider is selected.

    A provider is selected when any of its ``selection_envs`` is set. Keyless-
    capable providers (self-hosted / OpenAI-compatible endpoints, e.g. an
    ``OPENAI_BASE_URL`` with no key) are therefore valid: they are selected by
    their base URL, and the client falls back to a placeholder key downstream.
    In that case we log a warning rather than fail.
    An ``ANTHROPIC_BASE_URL`` alone reports its missing key without selecting
    Anthropic when another provider is configured.
    Ambiguity detection (more than one selected provider) is preserved so a
    stray second key is still surfaced, and a key set for an unselected provider
    (e.g. LITELLM_API_KEY without LITELLM_BASE_URL) is reported rather than
    silently ignored.
    """
    hints = _unselected_key_hints()
    selected = selected_providers()
    if not selected:
        anthropic = LLM_PROVIDERS["anthropic"]
        if os.getenv("ANTHROPIC_BASE_URL") and not anthropic.has_real_api_key():
            raise LLMConfigError("Provider 'anthropic' requires ANTHROPIC_API_KEY.")
        message = f"No LLM provider selected. Set one of: {', '.join(_all_selection_envs())}."
        raise LLMConfigError(" ".join([message, *hints]))
    if len(selected) > 1:
        raise LLMConfigError(f"Multiple LLM providers selected ({', '.join(selected)}); please set only one.")
    for hint in hints:
        logger.warning(hint)

    (name,) = selected
    config = LLM_PROVIDERS[name]
    if config.keyless_capable and not config.has_real_api_key():
        logger.warning(
            "Provider '%s' is selected via a base URL with no %s set; "
            "treating as a keyless local endpoint (a placeholder key is used).",
            name,
            config.api_key_env,
        )


def initialize_agent_llm(model_override: str | None = None) -> BaseChatModel:
    selected_model = model_override or os.getenv("AGENT_MODEL") or _agent_model_override
    model, model_name = _initialize_llm(selected_model)
    MONITORING_CALLBACK.model_name = model_name
    return model


def get_current_agent_context_window() -> ContextWindow:
    """Context window for the currently selected agent provider/model.

    Resolves the first selected provider (same rule as ``_initialize_llm``) on
    every call. ``get_context_window`` handles its own caching, so this is
    cheap enough to call without a module-level cache.
    """
    resolved = _resolve_selected_provider(os.getenv("AGENT_MODEL") or _agent_model_override)
    if resolved is not None:
        name, _config, model_name = resolved
        ctx = get_context_window(name, model_name)
        if name == "openrouter" and ctx.is_fallback:
            return _OPENROUTER_FALLBACK_CONTEXT_WINDOW
        return ctx
    return ContextWindow(ModelCapabilities.FALLBACK_INPUT, ModelCapabilities.FALLBACK_OUTPUT, is_fallback=True)


def get_current_agent_model_ref() -> str:
    """``provider/model`` for the currently active agent LLM, or ``"unknown"``."""
    resolved = _resolve_selected_provider(os.getenv("AGENT_MODEL") or _agent_model_override)
    if resolved is None:
        return "unknown"
    name, _config, model_name = resolved
    return f"{name}/{model_name}"


def get_current_prompt_profile(llm: BaseChatModel | None = None) -> PromptProfile:
    """Choose prompts for the active model without changing model defaults."""
    if llm is not None:
        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
        if model_name:
            return resolve_prompt_profile(str(model_name))
    return resolve_prompt_profile(get_current_agent_model_ref())


def current_provider_key_context() -> tuple[str, str]:
    """The selected provider name and a masked key tail, for auth-error messages.

    Returns ``(provider_name, key_tail)`` where ``key_tail`` is the last 4
    characters of the provider's key (or ``"unknown"`` when no provider is
    selected or the SDK reads its own credentials, e.g. AWS/Ollama). Never
    returns the full secret — only enough for the user to recognize which key.
    """
    selected = selected_providers()
    if not selected:
        return "unknown", "unknown"
    name = selected[0]
    key = LLM_PROVIDERS[name].get_api_key()
    key_tail = key[-4:] if key and len(key) >= 4 else "unknown"
    return name, key_tail


def supports_prompt_caching(llm: BaseChatModel) -> bool:
    """Return True when *llm* supports ephemeral prompt caching.

    Only Anthropic's langchain integration exposes ``cache_control`` blocks
    today; other providers either cache transparently or not at all.
    """
    return llm.__class__.__module__.startswith("langchain_anthropic")


def supports_json_mode(llm: BaseChatModel) -> bool:
    """True when *llm* takes OpenAI's ``response_format={"type": "json_object"}``."""
    return isinstance(llm, ChatOpenAI)
