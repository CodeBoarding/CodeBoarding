"""Deterministic repairs for parsed LLM agent outputs."""

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Protocol

from agents.agent_responses import (
    ClusterAnalysis,
    Component,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.cluster_ids import CodeBoardingClusterIds
from agents.scope_ids import ROOT_SCOPE_ID
from agents.scope_operations import (
    EXISTING_COMPONENT_ACTIONS,
    cluster_member_qnames,
    cluster_ref_from_scoped_ref,
    normalize_component_name,
)
from diagram_analysis.cluster_delta import ClusterRef
from static_analyzer.graph import ClusterResult
from static_analyzer.reference_resolver import StaticReferenceResolver

logger = logging.getLogger(__name__)

_KEY_ENTITY_METADATA_ACTIONS = frozenset(
    {
        ScopeOperationAction.CREATE_COMPONENT,
        ScopeOperationAction.UPDATE_COMPONENT,
    }
)


class ComponentRepairTarget(Protocol):
    components: list[Component]


@dataclass
class ComponentRepairContext:
    reference_resolver: StaticReferenceResolver
    llm_cluster_analysis: ClusterAnalysis
    cluster_results: dict[str, ClusterResult] = field(default_factory=dict)


@dataclass
class ScopeOperationRepairContext:
    reference_resolver: StaticReferenceResolver
    allowed_key_entity_qnames: set[str]
    component_ids_by_cluster_ref: dict[ClusterRef, str] = field(default_factory=dict)
    component_ids_by_name: dict[str, str] = field(default_factory=dict)
    scope_id: str = ROOT_SCOPE_ID
    actionable_cluster_refs: set[ClusterRef] = field(default_factory=set)
    owned_cluster_ids_by_component_id: dict[str, set[str]] = field(default_factory=dict)


def repair_unambiguous_routing_and_optional_key_entity_metadata(
    decision: ScopeUpdateDecision,
    context: ScopeOperationRepairContext,
) -> None:
    """Repair deterministic routing and optional key-entity metadata defects."""
    routed_operations = _repair_unambiguous_operation_routing(decision, context)
    trimmed_refs = _drop_redundant_owned_cluster_refs(decision, context)
    canonicalized_qnames, dropped_qnames = _repair_optional_key_entity_metadata(
        decision,
        context,
    )

    if routed_operations or trimmed_refs or canonicalized_qnames:
        logger.info(
            "Repaired incremental plan: routed %d operation(s), trimmed %d redundant owned cluster ref(s), "
            "canonicalized %d key-entity qname(s)",
            routed_operations,
            trimmed_refs,
            canonicalized_qnames,
        )
    if dropped_qnames:
        logger.warning("Dropped unresolved optional key entities: %s", sorted(dropped_qnames))


def _drop_redundant_owned_cluster_refs(
    decision: ScopeUpdateDecision,
    context: ScopeOperationRepairContext,
) -> int:
    """Trim cluster_refs an existing-component op re-lists that it already owns and that did not change.

    Why: the planner echoes a touched component's full ``clusters=[...]`` display, but only
    changed clusters are actionable. Re-listing owned-but-unchanged clusters is a downstream
    no-op (they are preserved when absent), so normalize the plan back to the intended
    actionable-only shape instead of failing the strict cluster_ref validator. A ref owned by a
    *different* component is left in place so genuine moves and cross-component theft still surface.

    An ``update_component`` that has no changed cluster to account for is left as-is, so the
    validator rejects it rather than trimming it to an empty update that would still apply
    name/description to an unchanged component (``create_component`` guards the same way).
    """
    prefix = CodeBoardingClusterIds.prefix_for_scope(context.scope_id)
    trimmed = 0
    for operation in decision.operations:
        if operation.action not in EXISTING_COMPONENT_ACTIONS or operation.component_id is None:
            continue
        if operation.action == ScopeOperationAction.UPDATE_COMPONENT and not any(
            cluster_ref_from_scoped_ref(ref) in context.actionable_cluster_refs for ref in operation.cluster_refs
        ):
            continue
        owned = context.owned_cluster_ids_by_component_id.get(operation.component_id, set())
        kept: list[ScopedClusterRef] = []
        for ref in operation.cluster_refs:
            source_cluster_id = CodeBoardingClusterIds.qualify_local_id(
                CodeBoardingClusterIds.from_graph_id(ref.cluster_id), prefix
            )
            redundant = source_cluster_id in owned and cluster_ref_from_scoped_ref(ref) not in (
                context.actionable_cluster_refs
            )
            if redundant:
                trimmed += 1
                continue
            kept.append(ref)
        operation.cluster_refs = kept
    return trimmed


def _repair_unambiguous_operation_routing(
    decision: ScopeUpdateDecision,
    context: ScopeOperationRepairContext,
) -> int:
    routed_operations = 0
    for operation in decision.operations:
        needs_routing_repair = operation.component_id is None and operation.action in EXISTING_COMPONENT_ACTIONS
        if needs_routing_repair:
            component_id = _resolve_unambiguous_component_id(operation, context)
            if component_id is not None:
                operation.component_id = component_id
                routed_operations += 1
        if operation.action == ScopeOperationAction.NOOP:
            operation.name = None
            operation.description = None
            operation.key_entities = []
    return routed_operations


def _resolve_unambiguous_component_id(
    operation: ScopeOperation,
    context: ScopeOperationRepairContext,
) -> str | None:
    refs = {cluster_ref_from_scoped_ref(ref) for ref in operation.cluster_refs}
    owner_ids = {
        context.component_ids_by_cluster_ref[ref] for ref in refs if ref in context.component_ids_by_cluster_ref
    }

    # Existing cluster ownership is stronger evidence than a mutable component name.
    if len(owner_ids) == 1:
        return next(iter(owner_ids))

    has_no_known_owner = not owner_ids
    if has_no_known_owner and operation.name:
        return context.component_ids_by_name.get(normalize_component_name(operation.name))
    return None


def _repair_optional_key_entity_metadata(
    decision: ScopeUpdateDecision,
    context: ScopeOperationRepairContext,
) -> tuple[int, set[str]]:
    canonicalized_qnames = 0
    dropped_qnames: set[str] = set()
    for operation in decision.operations:
        has_optional_key_entity_metadata = operation.action in _KEY_ENTITY_METADATA_ACTIONS and bool(
            operation.key_entities
        )
        if not has_optional_key_entity_metadata:
            continue

        repair = context.reference_resolver.repair_key_entity_references(
            operation.key_entities,
            allowed_qnames=context.allowed_key_entity_qnames,
        )
        operation.key_entities = repair.references[:5]
        canonicalized_qnames += repair.canonicalized_count
        dropped_qnames.update(repair.unresolved_qnames)
    return canonicalized_qnames, dropped_qnames


def repair_component_group_names(result: ComponentRepairTarget, context: ComponentRepairContext) -> None:
    """Canonicalize unambiguous component source-group names."""
    expected_group_names = {group.name for group in context.llm_cluster_analysis.cluster_components}
    canonical_names = {_normalize_group_name(name): name for name in expected_group_names}
    corrected_count = 0

    for component in result.components:
        corrected_names: list[str] = []
        for group_name in component.source_group_names:
            canonical_name = _canonical_group_name(group_name, canonical_names)
            if canonical_name is None:
                corrected_names.append(group_name)
                continue
            corrected_names.append(canonical_name)
            if canonical_name != group_name:
                corrected_count += 1
        component.source_group_names = corrected_names

    if corrected_count:
        logger.info("Repaired %d component source-group name(s)", corrected_count)


def _canonical_group_name(group_name: str, canonical_names: dict[str, str]) -> str | None:
    normalized_name = _normalize_group_name(group_name)
    exact_match = canonical_names.get(normalized_name)
    if exact_match is not None:
        return exact_match
    return _fuzzy_match_group_name(normalized_name, canonical_names)


def _normalize_group_name(name: str) -> str:
    normalized = re.sub(r"\s+", " ", name.lower().strip())
    normalized = re.sub(r"[()&/\\,\-–—]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _fuzzy_match_group_name(
    normalized_name: str,
    canonical_names: dict[str, str],
    threshold: float = 0.75,
) -> str | None:
    best_score = 0.0
    best_match: str | None = None
    for candidate, canonical_name in canonical_names.items():
        score = SequenceMatcher(None, normalized_name, candidate).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_match = canonical_name
    return best_match


def repair_key_entities(result: ComponentRepairTarget, context: ComponentRepairContext) -> None:
    """Resolve key entities and remove references outside the current scope."""
    nodes_in_scope = cluster_member_qnames(context.cluster_results)
    canonicalized_count = 0
    dropped_qnames: set[str] = set()

    for component in result.components:
        repair = context.reference_resolver.repair_key_entity_references(
            component.key_entities,
            allowed_qnames=nodes_in_scope if context.cluster_results else None,
        )
        canonicalized_count += repair.canonicalized_count
        dropped_qnames.update(repair.unresolved_qnames)
        component.key_entities = repair.references

    if canonicalized_count:
        logger.info("Repaired %d key-entity qualified name(s)", canonicalized_count)
    if dropped_qnames:
        logger.info("Dropped invalid or out-of-scope key entities: %s", sorted(dropped_qnames))
