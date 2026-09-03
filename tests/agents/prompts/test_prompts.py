from agents.prompts import (
    get_meta_information_prompt,
    get_system_meta_analysis_message,
    get_tree_plan_message,
    get_validation_feedback_message,
)


def test_retained_agents_have_shared_prompts() -> None:
    assert "project metadata" in get_system_meta_analysis_message()
    assert "project '{project_name}'" in get_meta_information_prompt()
    assert "candidate groups" in get_tree_plan_message()
    assert "{feedback_list}" in get_validation_feedback_message()
