import logging
import os
from dataclasses import dataclass, field
from typing import Any, Type

from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_cerebras import ChatCerebras
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from agents.constants import LLMDefaults
from agents.prompts.prompt_factory import LLMType, initialize_global_factory
from monitoring.callbacks import MonitoringCallback

# Initialize global monitoring callback with its own stats container to avoid ContextVar dependency
from monitoring.stats import RunStats

MONITORING_CALLBACK = MonitoringCallback(stats_container=RunStats())

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

    def get_api_key(self) -> str | None:
        return os.getenv(self.api_key_env)

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
        parsing_model="openai/gpt-oss-120b",  # Use OpenAI model for parsing to avoid trustcall compatibility issues with Gemini
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
        agent_model="claude-3-7-sonnet-20250219",
        parsing_model="claude-3-haiku-20240307",
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
        agent_model="gemini-3-flash",
        parsing_model="gemini-3-flash",
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
        agent_model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        parsing_model="us.anthropic.claude-3-haiku-20240307-v1:0",
        llm_type=LLMType.CLAUDE,
        extra_args={
            "max_tokens": 4096,
            "region_name": lambda: os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            "credentials_profile_name": None,
        },
    ),
    "cerebras": LLMConfig(
        chat_class=ChatCerebras,
        api_key_env="CEREBRAS_API_KEY",
        agent_model="gpt-oss-120b",
        parsing_model="llama3.1-8b",
        llm_type=LLMType.GPT4,
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
        agent_temperature=0.1,
        parsing_temperature=0.1,
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
        agent_model="glm-4-flash",
        parsing_model="glm-4-flash",
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
        agent_model="anthropic/claude-3.7-sonnet",
        parsing_model="anthropic/claude-3-haiku",
        llm_type=LLMType.CLAUDE,
        extra_args={
            "base_url": lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "max_tokens": None,
            "timeout": None,
            "max_retries": 0,
        },
    ),
}


def _initialize_llm(
    model_override: str | None,
    model_attr: str,
    temperature_attr: str,
    log_prefix: str,
    init_factory: bool = False,
) -> tuple[BaseChatModel, str]:
    for name, config in LLM_PROVIDERS.items():
        if not config.is_active():
            continue

        model_name = model_override or getattr(config, model_attr)

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

        if name not in ["aws", "ollama"]:
            api_key = config.get_api_key()
            kwargs["api_key"] = api_key or "no-key-required"

        model = config.chat_class(**kwargs)  # type: ignore[call-arg, arg-type]
        return model, model_name

    required_vars = []
    for config in LLM_PROVIDERS.values():
        required_vars.append(config.api_key_env)
        required_vars.extend(config.alt_env_vars)

    raise ValueError(f"No valid LLM configuration found. Please set one of: {', '.join(sorted(set(required_vars)))}")


def validate_api_key_provided() -> None:
    """Raise ValueError if zero or more than one LLM provider key is configured."""
    active = [name for name, config in LLM_PROVIDERS.items() if config.is_active()]
    if not active:
        required = sorted({config.api_key_env for config in LLM_PROVIDERS.values()})
        raise ValueError(f"No LLM provider API key found. Set one of: {', '.join(required)}")
    if len(active) > 1:
        raise ValueError(f"Multiple LLM provider keys detected ({', '.join(active)}); please set only one.")


def initialize_agent_llm(model_override: str | None = None) -> BaseChatModel:
    model, model_name = _initialize_llm(model_override, "agent_model", "agent_temperature", "", init_factory=True)
    MONITORING_CALLBACK.model_name = model_name
    return model


def initialize_parsing_llm(model_override: str | None = None) -> BaseChatModel:
    model, _ = _initialize_llm(model_override, "parsing_model", "parsing_temperature", "Extractor ")
    return model


def initialize_llms() -> tuple[BaseChatModel, BaseChatModel]:
    agent_llm = initialize_agent_llm(_agent_model_override or os.getenv("AGENT_MODEL"))
    parsing_llm = initialize_parsing_llm(_parsing_model_override or os.getenv("PARSING_MODEL"))
    return agent_llm, parsing_llm
