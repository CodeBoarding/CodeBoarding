import os
from dataclasses import dataclass, field
from typing import Type, Dict, Any, Optional, Callable

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_aws import ChatBedrockConverse
from langchain_cerebras import ChatCerebras
from langchain_ollama import ChatOllama


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


# Define supported providers in priority order
LLM_PROVIDERS = {
    "openai": LLMConfig(
        chat_class=ChatOpenAI,
        api_key_env="OPENAI_API_KEY",
        agent_model="gpt-4o",
        parsing_model="gpt-4o-mini",
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
        agent_temperature=0.1,
        parsing_temperature=0.1,
        extra_args={
            "base_url": lambda: os.getenv("OLLAMA_BASE_URL"),
        },
    ),
}
