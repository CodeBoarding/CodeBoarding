import os
import logging
from dataclasses import dataclass, field
from typing import Type, Any

from agents.prompts.prompt_factory import initialize_global_factory
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_aws import ChatBedrockConverse
from langchain_cerebras import ChatCerebras
from langchain_ollama import ChatOllama

from agents.prompts.prompt_factory import LLMType

logger = logging.getLogger(__name__)


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
    agent_temperature: float = 0.1
    parsing_temperature: float = 0
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


def detect_llm_type_from_model(model_name: str) -> LLMType:
    """
    Detect the LLM type/family from the model name.
    This determines which prompt factory to use.

    Args:
        model_name: The model name (e.g., "gpt-4o", "claude-3-7-sonnet", "gemini-2.5-flash")

    Returns:
        The detected LLMType enum value
    """
    model_lower = model_name.lower()

    # DeepSeek family
    if "deepseek" in model_lower:
        return LLMType.DEEPSEEK

    # GLM family (Zhipu AI)
    if "glm" in model_lower:
        return LLMType.GLM

    # Kimi family (Moonshot AI)
    if "kimi" in model_lower or "moonshot" in model_lower:
        return LLMType.KIMI

    # GPT family (OpenAI, O1, O3, etc.)
    if any(pattern in model_lower for pattern in ["gpt-", "gpt4", "gpt5", "o1-", "o3-"]):
        return LLMType.GPT4

    # Claude family (Anthropic) - matches claude, opus, sonnet, haiku
    if any(pattern in model_lower for pattern in ["claude", "opus", "sonnet", "haiku"]):
        return LLMType.CLAUDE

    # Gemini family (Google)
    if "gemini" in model_lower:
        return LLMType.GEMINI_FLASH

    # Default fallback to Gemini (most permissive prompts)
    logger.warning(
        f"Could not detect LLM type from model name '{model_name}', " f"defaulting to GEMINI_FLASH prompt factory"
    )
    return LLMType.GEMINI_FLASH


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
        agent_model="google/gemini-2.5-flash",
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
        agent_model="gemini-2.5-flash",
        parsing_model="gemini-2.5-flash",
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
}


def initialize_agent_llm(model_override: str | None = None) -> BaseChatModel:
    # Import MONITORING_CALLBACK here to avoid circular import
    from agents.agent import MONITORING_CALLBACK

    for name, config in LLM_PROVIDERS.items():
        if not config.is_active():
            continue

        # Determine final model name (override takes precedence over default)
        model_name = model_override or config.agent_model

        # Initialize global prompt factory based on ACTUAL model
        detected_llm_type = detect_llm_type_from_model(model_name)
        initialize_global_factory(detected_llm_type)
        logger.info(
            f"Initialized prompt factory for {name} provider with model '{model_name}' "
            f"-> {detected_llm_type.value} prompt factory"
        )

        logger.info(f"Using {name.title()} LLM with model: {model_name}")

        kwargs = {
            "model": model_name,
            "temperature": config.agent_temperature,
        }
        kwargs.update(config.get_resolved_extra_args())

        if name not in ["aws", "ollama"]:
            api_key = config.get_api_key()
            kwargs["api_key"] = api_key or "no-key-required"

        model = config.chat_class(**kwargs)  # type: ignore[call-arg, arg-type]

        # Update global monitoring callback
        MONITORING_CALLBACK.model_name = model_name
        return model

    # Dynamically build error message with all possible env vars
    required_vars = []
    for config in LLM_PROVIDERS.values():
        required_vars.append(config.api_key_env)
        required_vars.extend(config.alt_env_vars)

    raise ValueError(f"No valid LLM configuration found. Please set one of: {', '.join(sorted(set(required_vars)))}")


def initialize_parsing_llm(model_override: str | None = None) -> BaseChatModel:
    for name, config in LLM_PROVIDERS.items():
        if not config.is_active():
            continue

        model_name = model_override or config.parsing_model

        logger.info(f"Using {name.title()} Extractor LLM with model: {model_name}")

        kwargs = {
            "model": model_name,
            "temperature": config.parsing_temperature,
        }
        kwargs.update(config.get_resolved_extra_args())

        if name not in ["aws", "ollama"]:
            api_key = config.get_api_key()
            kwargs["api_key"] = api_key or "no-key-required"

        model = config.chat_class(**kwargs)  # type: ignore[call-arg, arg-type]
        return model

    # Dynamically build error message with all possible env vars
    required_vars = []
    for config in LLM_PROVIDERS.values():
        required_vars.append(config.api_key_env)
        required_vars.extend(config.alt_env_vars)

    raise ValueError(f"No valid LLM configuration found. Please set one of: {', '.join(sorted(set(required_vars)))}")


def initialize_llms() -> tuple[BaseChatModel, BaseChatModel]:
    agent_llm = initialize_agent_llm(os.getenv("AGENT_MODEL"))
    parsing_llm = initialize_parsing_llm(os.getenv("PARSING_MODEL"))
    return agent_llm, parsing_llm
