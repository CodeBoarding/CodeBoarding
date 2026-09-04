"""Prompt profiles for the model families already supported by LLM config."""

from enum import StrEnum


class PromptProfile(StrEnum):
    """Instruction style used by tree planning and semantic scope analysis."""

    STANDARD = "standard"
    STRICT = "strict"


_STRICT_MODEL_MARKERS = (
    "deepseek",
    "glm",
    "kimi",
    "moonshot",
    "qwen",
)


def resolve_prompt_profile(model_ref: str) -> PromptProfile:
    """Infer an instruction style from an existing configured model name."""
    model_ref = model_ref.casefold()
    if any(marker in model_ref for marker in _STRICT_MODEL_MARKERS):
        return PromptProfile.STRICT
    return PromptProfile.STANDARD
