"""
Prompt Factory Module

This module provides a factory for dynamically selecting prompts based on LLM type.
"""

import logging
from enum import StrEnum
from .abstract_prompt_factory import AbstractPromptFactory
from .gemini_flash_prompts import GeminiFlashPromptFactory
from .gpt_prompts import GPTPromptFactory
from .claude_prompts import ClaudePromptFactory
from .deepseek_prompts import DeepSeekPromptFactory
from .glm_prompts import GLMPromptFactory
from .kimi_prompts import KimiPromptFactory

logger = logging.getLogger(__name__)


class LLMType(StrEnum):
    GEMINI_FLASH = "gemini_flash"
    CLAUDE = "claude"
    CLAUDE_SONNET = "claude_sonnet"
    GPT4 = "gpt4"
    DEEPSEEK = "deepseek"
    GLM = "glm"
    KIMI = "kimi"

    @classmethod
    def from_model_name(cls, model_name: str) -> "LLMType":
        model_lower = model_name.lower().strip()

        if "deepseek" in model_lower:
            return cls.DEEPSEEK
        if "glm" in model_lower:
            return cls.GLM
        if "kimi" in model_lower or "moonshot" in model_lower:
            return cls.KIMI
        if any(pattern in model_lower for pattern in ["gpt-", "gpt4", "gpt5", "o1-", "o3-"]):
            return cls.GPT4
        if any(pattern in model_lower for pattern in ["claude", "opus", "sonnet", "haiku"]):
            return cls.CLAUDE
        if "gemini" in model_lower:
            return cls.GEMINI_FLASH

        return cls.GEMINI_FLASH


class PromptFactory:
    """Factory class for dynamically selecting prompts based on LLM type."""

    def __init__(self, llm_type: LLMType = LLMType.GEMINI_FLASH):
        self.llm_type = llm_type
        self._prompt_factory: AbstractPromptFactory = self._create_prompt_factory()

    def _create_prompt_factory(self) -> AbstractPromptFactory:
        """Create the appropriate prompt factory based on LLM type."""
        match self.llm_type:
            case LLMType.GEMINI_FLASH:
                return GeminiFlashPromptFactory()

            case LLMType.CLAUDE | LLMType.CLAUDE_SONNET:
                return ClaudePromptFactory()

            case LLMType.GPT4:
                return GPTPromptFactory()

            case LLMType.DEEPSEEK:
                return DeepSeekPromptFactory()

            case LLMType.GLM:
                return GLMPromptFactory()

            case LLMType.KIMI:
                return KimiPromptFactory()

            case _:
                # Default fallback
                return GeminiFlashPromptFactory()

    def get_prompt(self, prompt_name: str) -> str:
        """Get a specific prompt by name."""
        method_name = f"get_{prompt_name.lower()}"
        if hasattr(self._prompt_factory, method_name):
            return getattr(self._prompt_factory, method_name)()
        else:
            raise AttributeError(f"Prompt method '{method_name}' not found in factory")

    def get_all_prompts(self) -> dict[str, str]:
        prompts = {}
        for method_name in dir(self._prompt_factory):
            if method_name.startswith("get_") and not method_name.startswith("_"):
                try:
                    prompt_value = getattr(self._prompt_factory, method_name)()
                    constant_name = method_name[4:].upper()
                    prompts[constant_name] = prompt_value
                except Exception:
                    continue
        return prompts


# Global factory instance - will be initialized by configuration
_global_factory: PromptFactory | None = None


def initialize_global_factory(llm_type: LLMType = LLMType.GEMINI_FLASH):
    """Initialize the global prompt factory."""
    global _global_factory
    _global_factory = PromptFactory(llm_type)
    factory_class_name = _global_factory._prompt_factory.__class__.__name__
    logger.info(f"Initialized global prompt factory: {factory_class_name} (LLM type: {llm_type.value})")


def get_global_factory() -> PromptFactory:
    """Get the global prompt factory instance."""
    global _global_factory
    if _global_factory is None:
        # Default initialization if not set
        initialize_global_factory()
    assert _global_factory is not None  # After initialization, it should not be None
    return _global_factory


def get_prompt(prompt_name: str) -> str:
    """Convenience function to get a prompt using the global factory."""
    return get_global_factory().get_prompt(prompt_name)


# Convenience functions for backward compatibility - now use the factory methods directly
def get_system_message() -> str:
    return get_global_factory()._prompt_factory.get_system_message()


def get_cluster_grouping_message() -> str:
    return get_global_factory()._prompt_factory.get_cluster_grouping_message()


def get_final_analysis_message() -> str:
    return get_global_factory()._prompt_factory.get_final_analysis_message()


def get_planner_system_message() -> str:
    return get_global_factory()._prompt_factory.get_planner_system_message()


def get_expansion_prompt() -> str:
    return get_global_factory()._prompt_factory.get_expansion_prompt()


def get_system_meta_analysis_message() -> str:
    return get_global_factory()._prompt_factory.get_system_meta_analysis_message()


def get_meta_information_prompt() -> str:
    return get_global_factory()._prompt_factory.get_meta_information_prompt()


def get_file_classification_message() -> str:
    return get_global_factory()._prompt_factory.get_file_classification_message()


def get_unassigned_files_classification_message() -> str:
    return get_global_factory()._prompt_factory.get_unassigned_files_classification_message()


def get_validation_feedback_message() -> str:
    return get_global_factory()._prompt_factory.get_validation_feedback_message()


def get_system_details_message() -> str:
    return get_global_factory()._prompt_factory.get_system_details_message()


def get_cfg_details_message() -> str:
    return get_global_factory()._prompt_factory.get_cfg_details_message()


def get_details_message() -> str:
    return get_global_factory()._prompt_factory.get_details_message()
