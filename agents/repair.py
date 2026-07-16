"""Deterministic repairs for parsed LLM agent outputs."""

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Protocol

from agents.agent_responses import ClusterAnalysis, Component
from static_analyzer.clustering import ClusterResult
from static_analyzer.reference_resolver import StaticReferenceResolver

logger = logging.getLogger(__name__)


def cluster_member_qnames(cluster_results: dict[str, ClusterResult]) -> set[str]:
    """Return every qualified name represented in a scope's clusters."""
    return {
        qualified_name
        for cluster_result in cluster_results.values()
        for members in cluster_result.clusters.values()
        for qualified_name in members
    }


class ComponentRepairTarget(Protocol):
    components: list[Component]


@dataclass
class ComponentRepairContext:
    reference_resolver: StaticReferenceResolver
    llm_cluster_analysis: ClusterAnalysis
    cluster_results: dict[str, ClusterResult] = field(default_factory=dict)


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
