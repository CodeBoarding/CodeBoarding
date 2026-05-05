import logging
from typing import TYPE_CHECKING

from agents.agent_responses import ComponentPatch, PatchOperation
from agents.agent import BaseAgent
from agents.prompts.patching import get_patching_system_message, get_patching_prompt

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from agents.agent_responses import Component, FileMethodGroup

logger = logging.getLogger(__name__)


class PatchingAgent(BaseAgent):
    """LLM agent responsible for deciding how to patch a component's analysis."""

    def __init__(
        self,
        repo_dir,
        static_analysis,
        agent_llm: "BaseChatModel",
        parsing_llm: "BaseChatModel",
    ):
        super().__init__(
            repo_dir,
            static_analysis,
            get_patching_system_message(),
            agent_llm,
            parsing_llm,
            tool_names=["read_source_reference"],
        )

    def decide_patch(
        self,
        component: "Component",
        added_methods: list[str],
        removed_methods: list[str],
    ) -> ComponentPatch:
        """Ask the LLM to provide a patch for the component's description."""
        prompt = get_patching_prompt(
            component_name=component.name,
            current_description=component.description,
            added_methods=added_methods,
            removed_methods=removed_methods,
        )

        return self._validation_invoke(
            prompt,
            ComponentPatch,
            max_validation_attempts=2,
        )
