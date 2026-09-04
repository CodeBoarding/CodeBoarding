from agents.prompts import (
    PromptProfile,
    get_scope_analysis_prompts,
    get_tree_plan_prompts,
    resolve_prompt_profile,
)


def test_standard_prompts_cover_both_remaining_model_tasks() -> None:
    scope_system, scope_task = get_scope_analysis_prompts(PromptProfile.STANDARD)
    tree_system, tree_task = get_tree_plan_prompts(PromptProfile.STANDARD)

    assert "deterministic component groups" in scope_system
    assert "{scope_context}" in scope_task
    assert "candidate groups" in tree_system
    assert "{groups}" in tree_task


def test_strict_prompts_add_output_checklists() -> None:
    scope_system, scope_task = get_scope_analysis_prompts(PromptProfile.STRICT)
    tree_system, tree_task = get_tree_plan_prompts(PromptProfile.STRICT)

    assert "Mandatory execution order" in scope_system
    assert "Final checklist" in scope_task
    assert "Do not explain" in tree_system
    assert "every supplied G-label appears exactly once" in tree_task


def test_local_model_families_resolve_to_strict_profile() -> None:
    for model in ("qwen3:30b", "deepseek-v4-flash", "zai/glm-4.7", "moonshot/kimi-k2"):
        assert resolve_prompt_profile(model) is PromptProfile.STRICT


def test_existing_strong_model_families_use_standard_profile() -> None:
    for model in ("gpt-4o", "google/gemini-3.8-flash", "anthropic/claude-sonnet-5"):
        assert resolve_prompt_profile(model) is PromptProfile.STANDARD
