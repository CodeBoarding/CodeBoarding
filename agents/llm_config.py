import os
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Type, Dict, Any, Optional, Callable

import instructor
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_aws import ChatBedrockConverse
from langchain_cerebras import ChatCerebras
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)


class InstructorProvider(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    BEDROCK = "bedrock"
    CEREBRAS = "cerebras"
    OLLAMA = "ollama"


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
    """

    chat_class: Type[BaseChatModel]
    api_key_env: str
    agent_model: str
    parsing_model: str
    instructor_provider: InstructorProvider
    agent_temperature: float = 0
    parsing_temperature: float = 0
    extra_args: Dict[str, Any] = field(default_factory=dict)
    alt_env_vars: list[str] = field(default_factory=list)

    def get_api_key(self) -> Optional[str]:
        return os.getenv(self.api_key_env)

    def is_active(self) -> bool:
        """Check if any of the environment variables (primary or alternate) are set."""
        if os.getenv(self.api_key_env):
            return True
        return any(os.getenv(var) for var in self.alt_env_vars)

    def get_resolved_extra_args(self) -> Dict[str, Any]:
        resolved = {}
        for k, v in self.extra_args.items():
            value = v() if callable(v) else v
            if value is not None:
                resolved[k] = value
        return resolved

    def create_instructor_client(self, model_name: str):
        """Create an instructor client for this provider."""
        api_key = self.get_api_key()

        if self.instructor_provider == InstructorProvider.OPENAI:
            base_url = self.get_resolved_extra_args().get("base_url")
            if base_url:
                from openai import OpenAI

                openai_client = OpenAI(api_key=api_key, base_url=base_url)
                return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)
            return instructor.from_provider(f"openai/{model_name}", api_key=api_key)

        if self.instructor_provider == InstructorProvider.GOOGLE:
            return instructor.from_provider(
                f"google/{model_name}",
                api_key=api_key,
                mode=instructor.Mode.MD_JSON,
            )

        if self.instructor_provider in (InstructorProvider.BEDROCK, InstructorProvider.OLLAMA):
            return instructor.from_provider(f"{self.instructor_provider.value}/{model_name}")

        provider_string = f"{self.instructor_provider.value}/{model_name}"
        kwargs = {"api_key": api_key} if api_key else {}
        return instructor.from_provider(provider_string, **kwargs)


# Define supported providers in priority order
LLM_PROVIDERS = {
    "openai": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="OPENAI_API_KEY",
        agent_model="gpt-4o",
        parsing_model="gpt-4o-mini",
        instructor_provider=InstructorProvider.OPENAI,
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
        agent_model="gemini-2.5-flash",
        parsing_model="gemini-2.5-flash",
        instructor_provider=InstructorProvider.OPENAI,
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
        instructor_provider=InstructorProvider.ANTHROPIC,
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
        instructor_provider=InstructorProvider.GOOGLE,
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
        instructor_provider=InstructorProvider.BEDROCK,
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
        instructor_provider=InstructorProvider.CEREBRAS,
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
        instructor_provider=InstructorProvider.OLLAMA,
        agent_temperature=0.1,
        parsing_temperature=0.1,
        extra_args={
            "base_url": lambda: os.getenv("OLLAMA_BASE_URL"),
        },
    ),
}


def get_active_config() -> tuple[str, LLMConfig]:
    """Get the first active LLM configuration."""
    for name, config in LLM_PROVIDERS.items():
        if config.is_active():
            return name, config
    required_vars = []
    for config in LLM_PROVIDERS.values():
        required_vars.append(config.api_key_env)
        required_vars.extend(config.alt_env_vars)
    raise ValueError(f"No valid LLM configuration found. Please set one of: {', '.join(sorted(set(required_vars)))}")


def create_instructor_client_from_env():
    """Create an instructor client using the active LLM configuration.

    Returns:
        tuple: (instructor_client, model_name)
    """
    _, config = get_active_config()
    parsing_model_override = os.getenv("PARSING_MODEL", None)
    model_name = parsing_model_override or config.parsing_model
    logger.info(f"Creating instructor client with provider: {config.instructor_provider.value}, model: {model_name}")
    client = config.create_instructor_client(model_name)
    return client, model_name


def create_llm_from_env(
    model_override: str | None = None, is_parsing: bool = False
) -> tuple[BaseChatModel, str, "LLMConfig"]:
    """Create an LLM using the active configuration from environment.

    Args:
        model_override: Optional model name to override the default.
        is_parsing: If True, use parsing model defaults, else agent model.

    Returns:
        tuple: (model, model_name, config)
    """
    name, config = get_active_config()

    default_model = config.parsing_model if is_parsing else config.agent_model
    model_name = model_override or default_model

    logger.info(f"Using {name.title()} {'Extractor ' if is_parsing else ''}LLM with model: {model_name}")

    kwargs = {
        "model": model_name,
        "temperature": config.parsing_temperature if is_parsing else config.agent_temperature,
    }
    kwargs.update(config.get_resolved_extra_args())

    if name not in ["aws", "ollama"]:
        api_key = config.get_api_key()
        kwargs["api_key"] = api_key or "no-key-required"

    model = config.chat_class(**kwargs)  # type: ignore[call-arg, arg-type]
    return model, model_name, config
