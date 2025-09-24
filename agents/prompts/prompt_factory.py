"""
Prompt Factory Module

This module provides a factory for dynamically selecting prompts based on LLM type and configuration flags.
It supports both bidirectional and unidirectional prompt variations.
"""

import importlib
from enum import Enum
from typing import Dict, Optional


class PromptType(Enum):
    """Enum for different prompt types."""
    BIDIRECTIONAL = "bidirectional"
    UNIDIRECTIONAL = "unidirectional"


class LLMType(Enum):
    """Enum for different LLM types."""
    GEMINI_FLASH = "gemini_flash"
    # Future LLM types can be added here
    # GPT4 = "gpt4"
    # CLAUDE = "claude"


class PromptFactory:
    """Factory class for dynamically selecting prompts based on LLM and configuration."""
    
    def __init__(self, llm_type: LLMType = LLMType.GEMINI_FLASH, prompt_type: PromptType = PromptType.BIDIRECTIONAL):
        self.llm_type = llm_type
        self.prompt_type = prompt_type
        self._prompt_module = None
        self._load_prompt_module()
    
    def _load_prompt_module(self):
        """Load the appropriate prompt module based on LLM type and prompt type."""
        module_name = f"agents.prompts.{self.llm_type.value}_prompts_{self.prompt_type.value}"
        try:
            self._prompt_module = importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(f"Could not import prompt module '{module_name}': {e}")
    
    def get_prompt(self, prompt_name: str) -> str:
        """Get a specific prompt by name."""
        if not hasattr(self._prompt_module, prompt_name):
            raise AttributeError(f"Prompt '{prompt_name}' not found in module {self._prompt_module.__name__}")
        return getattr(self._prompt_module, prompt_name)
    
    def get_all_prompts(self) -> Dict[str, str]:
        """Get all prompts from the current module."""
        prompts = {}
        for attr_name in dir(self._prompt_module):
            if not attr_name.startswith('_') and attr_name.isupper():
                prompts[attr_name] = getattr(self._prompt_module, attr_name)
        return prompts
    
    @classmethod
    def create_for_vscode_runnable(cls, use_unidirectional: bool = True) -> 'PromptFactory':
        """Create a prompt factory specifically for vscode_runnable usage."""
        prompt_type = PromptType.UNIDIRECTIONAL if use_unidirectional else PromptType.BIDIRECTIONAL
        return cls(LLMType.GEMINI_FLASH, prompt_type)
    
    @classmethod
    def create_for_llm(cls, llm_name: str, **kwargs) -> 'PromptFactory':
        """Create a prompt factory for a specific LLM."""
        # Map LLM names to types
        llm_mapping = {
            "gemini": LLMType.GEMINI_FLASH,
            "gemini_flash": LLMType.GEMINI_FLASH,
            # Future mappings can be added here
        }
        
        llm_type = llm_mapping.get(llm_name.lower(), LLMType.GEMINI_FLASH)
        prompt_type = kwargs.get('prompt_type', PromptType.BIDIRECTIONAL)
        
        return cls(llm_type, prompt_type)


# Global factory instance - will be initialized by configuration
_global_factory: Optional[PromptFactory] = None


def initialize_global_factory(llm_type: LLMType = LLMType.GEMINI_FLASH, 
                             prompt_type: PromptType = PromptType.BIDIRECTIONAL):
    """Initialize the global prompt factory."""
    global _global_factory
    _global_factory = PromptFactory(llm_type, prompt_type)


def get_global_factory() -> PromptFactory:
    """Get the global prompt factory instance."""
    if _global_factory is None:
        # Default initialization if not set
        initialize_global_factory()
    return _global_factory


def get_prompt(prompt_name: str) -> str:
    """Convenience function to get a prompt using the global factory."""
    return get_global_factory().get_prompt(prompt_name)


# Convenience functions for backward compatibility
def get_system_message() -> str:
    return get_prompt('SYSTEM_MESSAGE')


def get_cfg_message() -> str:
    return get_prompt('CFG_MESSAGE')


def get_source_message() -> str:
    return get_prompt('SOURCE_MESSAGE')


def get_classification_message() -> str:
    return get_prompt('CLASSIFICATION_MESSAGE')


def get_conclusive_analysis_message() -> str:
    return get_prompt('CONCLUSIVE_ANALYSIS_MESSAGE')


def get_feedback_message() -> str:
    return get_prompt('FEEDBACK_MESSAGE')


def get_system_details_message() -> str:
    return get_prompt('SYSTEM_DETAILS_MESSAGE')


def get_subcfg_details_message() -> str:
    return get_prompt('SUBCFG_DETAILS_MESSAGE')


def get_cfg_details_message() -> str:
    return get_prompt('CFG_DETAILS_MESSAGE')


def get_enhance_structure_message() -> str:
    return get_prompt('ENHANCE_STRUCTURE_MESSAGE')


def get_details_message() -> str:
    return get_prompt('DETAILS_MESSAGE')


def get_planner_system_message() -> str:
    return get_prompt('PLANNER_SYSTEM_MESSAGE')


def get_expansion_prompt() -> str:
    return get_prompt('EXPANSION_PROMPT')


def get_validator_system_message() -> str:
    return get_prompt('VALIDATOR_SYSTEM_MESSAGE')


def get_component_validation_component() -> str:
    return get_prompt('COMPONENT_VALIDATION_COMPONENT')


def get_relationships_validation() -> str:
    return get_prompt('RELATIONSHIPS_VALIDATION')


def get_system_diff_analysis_message() -> str:
    return get_prompt('SYSTEM_DIFF_ANALYSIS_MESSAGE')


def get_diff_analysis_message() -> str:
    return get_prompt('DIFF_ANALYSIS_MESSAGE')


def get_system_meta_analysis_message() -> str:
    return get_prompt('SYSTEM_META_ANALYSIS_MESSAGE')


def get_meta_information_prompt() -> str:
    return get_prompt('META_INFORMATION_PROMPT')


def get_file_classification_message() -> str:
    return get_prompt('FILE_CLASSIFICATION_MESSAGE')
