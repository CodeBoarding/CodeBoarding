"""Scope-local patch agent for incremental analysis."""

from collections import Counter
import logging
from pathlib import Path
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from trustcall import create_extractor

from agents.agent import CodeBoardingAgent
from agents.analysis_result_responses import LLMBaseModel
from agents.cluster_ids import CodeBoardingClusterIds
from agents.full_analysis_responses import ComponentApiSurfaces
from agents.incremental_responses import IncrementalRelationDrafts
from diagram_analysis.incremental.models import (
    ComponentContent,
    ComponentContentContext,
    ScopeDescription,
    ScopePartition,
    ScopePatchContext,
)
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)

PatchModel = TypeVar("PatchModel", bound=LLMBaseModel)
PATCH_EXTRACTION_ATTEMPTS = 2
PARTITION_VALIDATION_ATTEMPTS = 3


class IncrementalAgent(CodeBoardingAgent):
    """Patch one architecture scope while immutable siblings stay read-only."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ) -> None:
        super().__init__(
            repo_dir,
            static_analysis,
            (
                "You maintain an existing software architecture through minimal patches. "
                "Preserve stable boundaries and names unless changed source evidence requires an update. "
                "Never assign files or methods directly and never modify immutable components."
            ),
            agent_llm,
            parsing_llm,
        )

    def patch_partition(
        self,
        context: ScopePatchContext,
        existing: ScopePartition,
    ) -> ScopePartition:
        proposal = existing.model_dump_json(indent=2)
        prompt = f"""Patch the writable component partition for the affected part of one architecture scope.

Infomap owns the structural modules. You may combine whole modules, move a whole module between affected
components, split an existing component along module boundaries, or create a component. Never split a module.
Preserve the proposed partition when the source evidence does not justify a boundary change.

Every current module ID must occur exactly once. Group modules by responsibility; component identity is reconciled
deterministically after this step. Treat component_id values in the proposed document only as lineage hints.
Immutable components are read-only and must not be returned.

The proposal below is the complete writable document. It contains every allowed module ID exactly once. Module IDs
that do not occur in this proposal are frozen and forbidden. Never copy cluster IDs from descriptive component
context.

Writable ScopePartition proposal:
{proposal}

Exact allowed module IDs:
{CodeBoardingClusterIds.sort(context.expected_cluster_ids)}

Current compact architecture:
{context.architecture_outline}

Parent scope:
{context.parent}

Immutable direct children (read-only):
{context.immutable_components or "None"}

Mutable direct children:
{context.mutable_components or "None"}

Current Infomap modules in the mutable partition:
{context.current_modules or "None"}

Relevant method changes:
{context.method_changes or "None"}

Changed call boundaries:
{context.call_changes or "None"}

Patch only the ScopePartition document. Its dictionary keys are working identifiers and have no architectural
meaning. Group related module IDs under one PartitionGroup when they form one responsibility."""
        proposal_errors = self._partition_errors(existing, context)
        if proposal_errors:
            raise ValueError(f"Deterministic incremental partition is invalid: {'; '.join(proposal_errors)}")

        feedback = ""
        for attempt in range(1, PARTITION_VALIDATION_ATTEMPTS + 1):
            candidate = self._patch_existing(f"{prompt}{feedback}", existing)
            errors = self._partition_errors(candidate, context)
            if not errors:
                return candidate
            logger.warning(
                "Incremental partition validation failed on attempt %d/%d: %s",
                attempt,
                PARTITION_VALIDATION_ATTEMPTS,
                "; ".join(errors),
            )
            feedback = (
                "\n\nVALIDATION FAILURE IN THE PREVIOUS PATCH:\n- "
                + "\n- ".join(errors)
                + "\nRebuild the patch from the original writable proposal. Use every exact allowed module ID "
                "once and use no other IDs."
            )

        logger.error(
            "Partition patch remained invalid after %d attempts; retaining the deterministic proposal",
            PARTITION_VALIDATION_ATTEMPTS,
        )
        return existing.model_copy(deep=True)

    def patch_component_content(
        self,
        context: ComponentContentContext,
        existing: ComponentContent,
    ) -> ComponentContent:
        action = "Create" if context.is_new else "Patch"
        prompt = f"""{action} the component description using only the current assigned clusters.

Preserve its name, description, and key entities when they remain accurate. Update them when responsibilities were
added, removed, merged, or split. Key entities are qualified-name strings and must come from the supplied clusters.

Current compact architecture (read-only):
{context.architecture_outline}

Parent scope:
{context.parent}

Component ID: {context.component_id or "new component"}
New component: {context.is_new}

Assigned current clusters:
{context.current_clusters}

Relevant method changes:
{context.method_changes or "None"}

Changed call boundaries:
{context.call_changes or "None"}

{"Return a complete new ComponentContent document." if context.is_new else "Patch only name, description, and key_entity_qualified_names in the existing ComponentContent document."}"""
        candidate = (
            self._parse_invoke(prompt, ComponentContent) if context.is_new else self._patch_existing(prompt, existing)
        )
        candidate.key_entity_qualified_names = list(
            dict.fromkeys(
                qualified_name
                for qualified_name in candidate.key_entity_qualified_names
                if qualified_name in context.allowed_qualified_names
            )
        )
        if not candidate.name.strip() or not candidate.description.strip():
            if context.is_new:
                raise ValueError("A new incremental component requires a non-empty name and description")
            return existing
        return candidate

    def patch_scope_description(
        self,
        architecture_outline: str,
        parent: str,
        children: str,
        existing: ScopeDescription,
    ) -> ScopeDescription:
        prompt = f"""Patch this scope description so it accurately summarizes its updated direct children.

Keep the existing wording when the scope purpose did not change. Describe the scope's purpose and main flow, not
the incremental operation.

Current compact architecture (read-only):
{architecture_outline}

Scope:
{parent}

Updated direct children:
{children or "None"}"""
        candidate = self._patch_existing(prompt, existing)
        return candidate if candidate.description.strip() else existing

    def analyze_api_surfaces(
        self,
        component_context: str,
        static_call_evidence: str,
        affected_component_ids: set[str],
    ) -> ComponentApiSurfaces:
        prompt = f"""Analyze provided and consumed contracts for the supplied direct-child components.
Focus on components with IDs {sorted(affected_component_ids)} and their immediate interaction neighbors.
Use only real qualified symbols from the context. Do not create relation edges.

Components:
{component_context}

Verified static architectural evidence:
{static_call_evidence or "No static cross-component calls."}"""
        return self._parse_invoke(prompt, ComponentApiSurfaces)

    def analyze_relations(
        self,
        component_context: str,
        api_surfaces: ComponentApiSurfaces,
        static_edge_catalog: str,
        previous_relations: str,
        affected_component_ids: set[str],
    ) -> IncrementalRelationDrafts:
        prompt = f"""Describe directed contracts involving components {sorted(affected_component_ids)}.

You cannot create concrete method edges. Select key_static_edge_ids only from entries under "Verified CALL edge
IDs". Import and inheritance entries identify trustworthy architectural neighbors but are not concrete call edges.
For communication absent from the static evidence, provide concise evidence and real symbol
references from both endpoint components. A relation must connect two different component IDs; omit internal calls
within one component and omit speculative relations.

Components:
{component_context}

API surfaces:
{api_surfaces.llm_str()}

Verified static evidence and CALL-edge catalog:
{static_edge_catalog or "No verified static edges."}

Previous relation context:
{previous_relations or "None"}"""
        return self._parse_invoke(prompt, IncrementalRelationDrafts)

    def _patch_existing(self, prompt: str, existing: PatchModel) -> PatchModel:
        findings = self._invoke(prompt)
        extractor = create_extractor(
            self.parsing_llm,
            tools=[type(existing)],
            tool_choice=type(existing).__name__,
            enable_updates=True,
        )
        retry_feedback = ""
        for attempt in range(1, PATCH_EXTRACTION_ATTEMPTS + 1):
            result = extractor.invoke(
                {
                    "messages": [
                        HumanMessage(
                            content=(
                                "Apply the architecture agent's findings to the existing typed document using "
                                "minimal JSON Patch operations. Replace arrays as whole values instead of patching "
                                "fields inside array elements. Use replace only for paths present in the existing "
                                "document and use add for new paths."
                                f"{retry_feedback}\n\nOriginal task:\n{prompt}\n\nAgent findings:\n{findings}"
                            )
                        )
                    ],
                    "existing": {type(existing).__name__: existing.model_dump(mode="json")},
                }
            )
            responses = result.get("responses", [])
            if responses and isinstance(responses[0], type(existing)):
                return responses[0]
            logger.warning(
                "Trustcall returned no valid %s patch on attempt %d/%d",
                type(existing).__name__,
                attempt,
                PATCH_EXTRACTION_ATTEMPTS,
            )
            retry_feedback = (
                "\n\nThe previous JSON Patch could not be applied. Rebuild it against the exact existing "
                "document below; do not reuse an array index or object path that is absent."
            )

        complete_document = (
            f"Return the complete updated {type(existing).__name__} document. Preserve every existing value not "
            "explicitly changed by the findings.\n\n"
            f"Existing document:\n{existing.model_dump_json(indent=2)}\n\nAgent findings:\n{findings}"
        )
        try:
            return self._parse_response(prompt, complete_document, type(existing))
        except Exception as exc:
            raise ValueError(f"Could not patch {type(existing).__name__}") from exc

    @staticmethod
    def _partition_errors(candidate: ScopePartition, context: ScopePatchContext) -> list[str]:
        groups = list(candidate.groups.values())
        assignments = [cluster_id for group in groups for cluster_id in group.cluster_ids]
        assigned = set(assignments)
        assignment_counts = Counter(assignments)
        missing = CodeBoardingClusterIds.sort(context.expected_cluster_ids - assigned)
        unexpected = CodeBoardingClusterIds.sort(assigned - context.expected_cluster_ids)
        duplicate_modules = CodeBoardingClusterIds.sort(
            {cluster_id for cluster_id, count in assignment_counts.items() if count > 1}
        )
        empty_groups = sorted(key for key, group in candidate.groups.items() if not group.cluster_ids)
        errors: list[str] = []
        if missing:
            errors.append(f"Missing writable module IDs: {missing}")
        if unexpected:
            errors.append(f"Returned frozen or unknown module IDs: {unexpected}")
        if duplicate_modules:
            errors.append(f"Assigned module IDs more than once: {duplicate_modules}")
        if empty_groups:
            errors.append(f"Returned empty partition groups: {empty_groups}")
        return errors
