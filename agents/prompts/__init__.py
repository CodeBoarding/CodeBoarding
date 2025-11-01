"""
Prompts module - Dynamic prompt selection system

This module provides backward compatibility with the old prompt system while enabling
dynamic selection of prompts based on LLM type and configuration flags.

The system will automatically choose between bidirectional and unidirectional prompts
based on the configuration or runtime context.
"""

from .prompt_factory import (
    PromptFactory,
    PromptType,
    LLMType,
    initialize_global_factory,
    get_global_factory,
    get_prompt,
)

# Import all the convenience functions for backward compatibility
from .prompt_factory import (
    get_system_message,
    get_cfg_message,
    get_source_message,
    get_classification_message,
    get_conclusive_analysis_message,
    get_feedback_message,
    get_system_details_message,
    get_subcfg_details_message,
    get_cfg_details_message,
    get_enhance_structure_message,
    get_details_message,
    get_planner_system_message,
    get_expansion_prompt,
    get_validator_system_message,
    get_component_validation_component,
    get_relationships_validation,
    get_system_diff_analysis_message,
    get_diff_analysis_message,
    get_system_meta_analysis_message,
    get_meta_information_prompt,
    get_file_classification_message,
)


# For backward compatibility, expose the prompt constants directly
# These will be dynamically loaded from the appropriate module
def __getattr__(name: str):
    """
    Dynamic attribute access for backward compatibility.

    This allows the old import style:
    from agents.prompts import CFG_MESSAGE, SYSTEM_MESSAGE, etc.

    to work while using the dynamic prompt system under the hood.
    """
    try:
        return get_prompt(name)
    except (AttributeError, ImportError):
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


# Define what should be available when doing "from agents.prompts import *"
__all__ = [
    # Classes and functions
    "PromptFactory",
    "PromptType",
    "LLMType",
    "initialize_global_factory",
    "get_global_factory",
    "get_prompt",
    # Convenience functions
    "get_system_message",
    "get_cfg_message",
    "get_source_message",
    "get_classification_message",
    "get_conclusive_analysis_message",
    "get_feedback_message",
    "get_system_details_message",
    "get_subcfg_details_message",
    "get_cfg_details_message",
    "get_enhance_structure_message",
    "get_details_message",
    "get_planner_system_message",
    "get_expansion_prompt",
    "get_validator_system_message",
    "get_component_validation_component",
    "get_relationships_validation",
    "get_system_diff_analysis_message",
    "get_diff_analysis_message",
    "get_system_meta_analysis_message",
    "get_meta_information_prompt",
    "get_file_classification_message",
    # Prompt constants (available via __getattr__)
    "SYSTEM_MESSAGE",
    "CFG_MESSAGE",
    "SOURCE_MESSAGE",
    "CLASSIFICATION_MESSAGE",
    "CONCLUSIVE_ANALYSIS_MESSAGE",
    "FEEDBACK_MESSAGE",
    "SYSTEM_DETAILS_MESSAGE",
    "SUBCFG_DETAILS_MESSAGE",
    "CFG_DETAILS_MESSAGE",
    "ENHANCE_STRUCTURE_MESSAGE",
    "DETAILS_MESSAGE",
    "PLANNER_SYSTEM_MESSAGE",
    "EXPANSION_PROMPT",
    "VALIDATOR_SYSTEM_MESSAGE",
    "COMPONENT_VALIDATION_COMPONENT",
    "RELATIONSHIPS_VALIDATION",
    "SYSTEM_DIFF_ANALYSIS_MESSAGE",
    "DIFF_ANALYSIS_MESSAGE",
    "SYSTEM_META_ANALYSIS_MESSAGE",
    "META_INFORMATION_PROMPT",
    "FILE_CLASSIFICATION_MESSAGE",
]
