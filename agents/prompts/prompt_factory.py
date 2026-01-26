"""
Prompt Factory Module

This module provides a factory for dynamically selecting prompts based on LLM type and configuration flags.
It supports both bidirectional and unidirectional prompt variations.
"""

from enum import Enum
from .abstract_prompt_factory import AbstractPromptFactory
from .gemini_flash_prompts_bidirectional import GeminiFlashBidirectionalPromptFactory
from .gemini_flash_prompts_unidirectional import GeminiFlashUnidirectionalPromptFactory
from .gpt_prompts_bidirectional import GPTBidirectionalPromptFactory
from .gpt_prompts_unidirectional import GPTUnidirectionalPromptFactory
from .claude_prompts_bidirectional import ClaudeBidirectionalPromptFactory
from .claude_prompts_unidirectional import ClaudeUnidirectionalPromptFactory


class PromptType(Enum):
    """Enum for different prompt types."""

    BIDIRECTIONAL = "bidirectional"
    UNIDIRECTIONAL = "unidirectional"


class LLMType(Enum):
    """Enum for different LLM types."""

    GEMINI_FLASH = "gemini_flash"
    CLAUDE_SONNET = "claude_sonnet"
    CLAUDE = "claude"
    GPT4 = "gpt4"  # GPT-4 family optimized prompts
    VERCEL = "vercel"


class PromptFactory:
    """Factory class for dynamically selecting prompts based on LLM and configuration."""

    def __init__(self, llm_type: LLMType = LLMType.GEMINI_FLASH, prompt_type: PromptType = PromptType.BIDIRECTIONAL):
        self.llm_type = llm_type
        self.prompt_type = prompt_type
        self._prompt_factory: AbstractPromptFactory = self._create_prompt_factory()

    def _create_prompt_factory(self) -> AbstractPromptFactory:
        """Create the appropriate prompt factory based on LLM type and prompt type."""
        match self.llm_type:
            case LLMType.GEMINI_FLASH:
                if self.prompt_type == PromptType.BIDIRECTIONAL:
                    return GeminiFlashBidirectionalPromptFactory()
                return GeminiFlashUnidirectionalPromptFactory()

            case LLMType.CLAUDE | LLMType.CLAUDE_SONNET:
                if self.prompt_type == PromptType.BIDIRECTIONAL:
                    return ClaudeBidirectionalPromptFactory()
                return ClaudeUnidirectionalPromptFactory()

            case LLMType.GPT4 | LLMType.VERCEL:
                if self.prompt_type == PromptType.BIDIRECTIONAL:
                    return GPTBidirectionalPromptFactory()
                return GPTUnidirectionalPromptFactory()

            case _:
                # Default fallback
                return GeminiFlashBidirectionalPromptFactory()

    def get_prompt(self, prompt_name: str) -> str:
        """Get a specific prompt by name."""
        method_name = f"get_{prompt_name.lower()}"
        if hasattr(self._prompt_factory, method_name):
            return getattr(self._prompt_factory, method_name)()
        else:
            raise AttributeError(f"Prompt method '{method_name}' not found in factory")

    def get_all_prompts(self) -> dict[str, str]:
        """Get all prompts from the current factory."""
        prompts = {}
        # Get all methods that start with 'get_' and don't start with '_'
        for method_name in dir(self._prompt_factory):
            if method_name.startswith("get_") and not method_name.startswith("_"):
                try:
                    prompt_value = getattr(self._prompt_factory, method_name)()
                    # Convert method name to constant name (get_system_message -> SYSTEM_MESSAGE)
                    constant_name = method_name[4:].upper()  # Remove 'get_' and uppercase
                    prompts[constant_name] = prompt_value
                except Exception:
                    continue  # Skip methods that can't be called without parameters
        return prompts

    @classmethod
    def create_for_vscode_runnable(cls, use_unidirectional: bool = True) -> "PromptFactory":
        """Create a prompt factory specifically for vscode_runnable usage."""
        prompt_type = PromptType.UNIDIRECTIONAL if use_unidirectional else PromptType.BIDIRECTIONAL
        return cls(LLMType.GEMINI_FLASH, prompt_type)

    @classmethod
    def create_for_llm(cls, llm_name: str, **kwargs) -> "PromptFactory":
        """Create a prompt factory for a specific LLM."""
        # Map LLM names to types
        llm_mapping = {
            "gemini": LLMType.GEMINI_FLASH,
            "gemini_flash": LLMType.GEMINI_FLASH,
            "claude": LLMType.CLAUDE,
            "claude_sonnet": LLMType.CLAUDE_SONNET,
            "gpt4": LLMType.GPT4,
            "gpt-4": LLMType.GPT4,
            "openai": LLMType.GPT4,  # Default OpenAI to GPT4
            "vercel": LLMType.VERCEL,
        }

        llm_type = llm_mapping.get(llm_name.lower(), LLMType.GEMINI_FLASH)
        prompt_type = kwargs.get("prompt_type", PromptType.BIDIRECTIONAL)

        return cls(llm_type, prompt_type)


# Global factory instance - will be initialized by configuration
_global_factory: PromptFactory | None = None


def initialize_global_factory(
    llm_type: LLMType = LLMType.GEMINI_FLASH, prompt_type: PromptType = PromptType.BIDIRECTIONAL
):
    """Initialize the global prompt factory."""
    global _global_factory
    _global_factory = PromptFactory(llm_type, prompt_type)


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
