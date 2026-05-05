import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agents.agent_responses import PatchOperation, index_components_by_id
from agents.patching_agent import PatchingAgent
from llms import initialize_llms

if TYPE_CHECKING:
    from agents.agent_responses import AnalysisInsights, Component, FileMethodGroup
    from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)

COMPACTION_THRESHOLD = 5


class IncrementalPatcher:
    """Orchestrator for the Letta/Mem0-style patching flow."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: "StaticAnalysisResults",
    ):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis
        agent_llm, parsing_llm = initialize_llms()
        self.agent = PatchingAgent(repo_dir, static_analysis, agent_llm, parsing_llm)

    def patch_components(
        self,
        redetail_ids: set[str],
        root_analysis: "AnalysisInsights",
        sub_analyses: dict[str, "AnalysisInsights"],
        old_file_methods: dict[str, list["FileMethodGroup"]],
    ) -> set[str]:
        """Apply granular patches to touched components.

        Returns a subset of `redetail_ids` that actually need a full rewrite
        (because the LLM chose REWRITE or the compaction threshold was hit).
        """
        component_index = index_components_by_id(root_analysis, sub_analyses)
        full_rewrite_ids: set[str] = set()

        for cid in redetail_ids:
            component = component_index.get(cid)
            if not component:
                continue

            # 1. Compaction Check
            if component.patch_count >= COMPACTION_THRESHOLD:
                logger.info(f"Compaction threshold hit for component '{component.name}' ({cid}). Forcing rewrite.")
                full_rewrite_ids.add(cid)
                component.patch_count = 0
                continue

            # 2. Calculate Delta
            added, removed = self._calculate_method_delta(old_file_methods.get(cid, []), component.file_methods)

            if not added and not removed:
                logger.debug(f"No method delta for '{component.name}' ({cid}). Skipping patcher.")
                continue

            # 3. Ask the Agent for a Patch
            patch = self.agent.decide_patch(component, added, removed)
            logger.info(f"Patcher for '{component.name}' ({cid}): {patch.operation.value} - {patch.reasoning}")

            # 4. Apply the Patch
            if patch.operation == PatchOperation.NOOP:
                component.patch_count += 1
                continue

            if patch.operation == PatchOperation.FULL_REWRITE:
                full_rewrite_ids.add(cid)
                component.patch_count = 0
                continue

            self._apply_patch_to_component(component, patch)
            component.patch_count += 1

        return full_rewrite_ids

    def _calculate_method_delta(
        self, old_groups: list["FileMethodGroup"], new_groups: list["FileMethodGroup"]
    ) -> tuple[list[str], list[str]]:
        """Compute the set of added and removed method names."""
        old_methods = {m.qualified_name for g in old_groups for m in g.methods}
        new_methods = {m.qualified_name for g in new_groups for m in g.methods}

        added = sorted(list(new_methods - old_methods))
        removed = sorted(list(old_methods - new_methods))
        return added, removed

    def _apply_patch_to_component(self, component: "Component", patch: "ComponentPatch"):
        """Deterministically apply the patch to the component object."""
        if patch.updated_name:
            component.name = patch.updated_name

        if patch.operation == PatchOperation.APPEND:
            component.description = component.description.strip() + "\n\n" + patch.patch_text.strip()
        elif patch.operation == PatchOperation.REPLACE_SECTION:
            # For simplicity in this first version, REPLACE_SECTION replaces the whole description
            # but in a smarter way than a full re-generation from the detailer.
            component.description = patch.patch_text.strip()

        if patch.key_entities:
            component.key_entities = patch.key_entities
