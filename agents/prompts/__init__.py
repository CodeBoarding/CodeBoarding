"""Model-adapted prompts for tree planning and deterministic scope analysis."""

from agents.prompts.profiles import PromptProfile, resolve_prompt_profile
from agents.prompts.shared import (
    SCOPE_ANALYSIS_MESSAGE,
    SCOPE_ANALYSIS_SYSTEM_MESSAGE,
    STRICT_SCOPE_ANALYSIS_MESSAGE_SUFFIX,
    STRICT_SCOPE_ANALYSIS_SYSTEM_SUFFIX,
    STRICT_TREE_PLAN_MESSAGE_SUFFIX,
    STRICT_TREE_PLAN_SYSTEM_SUFFIX,
    TREE_PLAN_MESSAGE,
    TREE_PLAN_SYSTEM_MESSAGE,
)


def get_tree_plan_prompts(profile: PromptProfile) -> tuple[str, str]:
    """Return tree-planning system and task prompts for a model profile."""
    if profile is PromptProfile.STRICT:
        return (
            TREE_PLAN_SYSTEM_MESSAGE + STRICT_TREE_PLAN_SYSTEM_SUFFIX,
            TREE_PLAN_MESSAGE + STRICT_TREE_PLAN_MESSAGE_SUFFIX,
        )
    return TREE_PLAN_SYSTEM_MESSAGE, TREE_PLAN_MESSAGE


def get_scope_analysis_prompts(profile: PromptProfile) -> tuple[str, str]:
    """Return scope-analysis system and task prompts for a model profile."""
    if profile is PromptProfile.STRICT:
        return (
            SCOPE_ANALYSIS_SYSTEM_MESSAGE + STRICT_SCOPE_ANALYSIS_SYSTEM_SUFFIX,
            SCOPE_ANALYSIS_MESSAGE + STRICT_SCOPE_ANALYSIS_MESSAGE_SUFFIX,
        )
    return SCOPE_ANALYSIS_SYSTEM_MESSAGE, SCOPE_ANALYSIS_MESSAGE


__all__ = [
    "PromptProfile",
    "get_scope_analysis_prompts",
    "get_tree_plan_prompts",
    "resolve_prompt_profile",
]
