"""Prompts for metadata, tree planning, and deterministic scope analysis."""

from agents.prompts.shared import (
    META_INFORMATION_PROMPT,
    SCOPE_ANALYSIS_MESSAGE,
    SCOPE_ANALYSIS_SYSTEM_MESSAGE,
    SYSTEM_META_ANALYSIS_MESSAGE,
    TREE_PLAN_MESSAGE,
    TREE_PLAN_SYSTEM_MESSAGE,
    VALIDATION_FEEDBACK_MESSAGE,
)


def get_system_meta_analysis_message() -> str:
    return SYSTEM_META_ANALYSIS_MESSAGE


def get_meta_information_prompt() -> str:
    return META_INFORMATION_PROMPT


def get_validation_feedback_message() -> str:
    return VALIDATION_FEEDBACK_MESSAGE


def get_tree_plan_message() -> str:
    return TREE_PLAN_MESSAGE


__all__ = [
    "get_system_meta_analysis_message",
    "get_meta_information_prompt",
    "get_validation_feedback_message",
    "get_tree_plan_message",
    "SCOPE_ANALYSIS_MESSAGE",
    "SCOPE_ANALYSIS_SYSTEM_MESSAGE",
]
