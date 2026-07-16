"""Scope-local architecture and contract updates for incremental analysis."""

import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from agents.agent import CodeBoardingAgent
from agents.full_analysis_responses import ComponentApiSurfaces
from agents.incremental_responses import (
    IncrementalArchitecturePatch,
    IncrementalRelationDrafts,
)
from agents.validation import ValidationContext, validate_key_entities
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncrementalArchitectureContext:
    parent: str
    existing_components: str
    immutable_components: str
    mutable_clusters: str
    method_changes: str
    call_changes: str
    expected_cluster_ids: set[str]
    cluster_members: dict[str, set[str]]


class IncrementalAgent(CodeBoardingAgent):
    """Update one direct-child scope without receiving descendant analysis."""

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
                "You update one level of an existing software architecture. Preserve immutable components. "
                "Assign every mutable cluster exactly once. Never assign files or methods directly. "
                "Names and descriptions must describe only the current surviving implementation."
            ),
            agent_llm,
            parsing_llm,
        )

    def update_architecture(self, context: IncrementalArchitectureContext) -> IncrementalArchitecturePatch:
        prompt = f"""Update only the mutable partition of this direct-child scope.

Parent scope:
{context.parent}

Existing direct children:
{context.existing_components}

Immutable children (read-only; do not return or modify them):
{context.immutable_components or "None"}

Mutable current clusters:
{context.mutable_clusters or "None remain"}

Method-level changes:
{context.method_changes or "None"}

Changed call boundaries:
{context.call_changes or "None"}

Return replacement components for the mutable clusters only. You may merge, split, rename, add, or delete
components. Every cluster ID listed under mutable current clusters must occur exactly once, and no other cluster
ID may be returned. If all mutable code was deleted, return an empty components list. Do not define relations."""
        result = self._parse_invoke(prompt, IncrementalArchitecturePatch)
        assigned = [cluster_id for component in result.components for cluster_id in component.source_cluster_ids]
        assigned_set = set(assigned)
        if assigned_set != context.expected_cluster_ids or len(assigned) != len(assigned_set):
            missing = sorted(context.expected_cluster_ids - assigned_set)
            unexpected = sorted(assigned_set - context.expected_cluster_ids)
            duplicates = sorted({cluster_id for cluster_id in assigned if assigned.count(cluster_id) > 1})
            raise ValueError(
                "Invalid incremental cluster coverage: "
                f"missing={missing}, unexpected={unexpected}, duplicates={duplicates}"
            )
        for component in result.components:
            allowed_qnames = set().union(
                *(context.cluster_members[cluster_id] for cluster_id in component.source_cluster_ids)
            )
            repair = self.reference_resolver.repair_key_entity_references(
                component.key_entities,
                allowed_qnames=allowed_qnames,
            )
            component.key_entities = repair.references
        key_entity_validation = validate_key_entities(result, ValidationContext())
        if not key_entity_validation.is_valid:
            raise ValueError("; ".join(key_entity_validation.feedback_messages))
        return result

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

Verified static call evidence:
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

You cannot create concrete method edges. For static communication, select key_static_edge_ids only from the
verified catalog. For communication absent from the static catalog, provide concise evidence and real symbol
references from both endpoint components. Omit speculative relations.

Components:
{component_context}

API surfaces:
{api_surfaces.llm_str()}

Verified static-edge catalog:
{static_edge_catalog or "No verified static edges."}

Previous relation context:
{previous_relations or "None"}"""
        return self._parse_invoke(prompt, IncrementalRelationDrafts)
