"""Deterministic repairs for parsed LLM agent outputs."""

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Protocol

from agents.agent_responses import ClusterAnalysis, Component
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.reference_resolver import StaticReferenceResolver

logger = logging.getLogger(__name__)


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


def ensure_unique_key_entities(result: ComponentRepairTarget) -> None:
    """Keep each key_entity (by qualified_name) in exactly one component.

    If a key_entity appears in multiple components, keep it where it's most
    relevant: the component whose files contain its reference_file, else the
    first component that claimed it. Prevents documentation listing the same
    class/method as a "key entity" of multiple components.
    """
    logger.info("Ensuring key_entities are unique across components")

    seen_entities: dict[str, Component] = {}

    for component in result.components:
        entities_to_remove = []

        for key_entity in component.key_entities:
            qname = key_entity.qualified_name

            if qname in seen_entities:
                original_component = seen_entities[qname]
                ref_file = key_entity.reference_file

                component_files = component.file_paths()
                original_files = original_component.file_paths()
                current_has_file = ref_file and any(ref_file in f for f in component_files)
                original_has_file = ref_file and any(ref_file in f for f in original_files)

                if current_has_file and not original_has_file:
                    # Move to current component
                    original_component.key_entities = [
                        e for e in original_component.key_entities if e.qualified_name != qname
                    ]
                    seen_entities[qname] = component
                    logger.debug(f"Moved key_entity '{qname}' from {original_component.name} to {component.name}")
                else:
                    # Keep in original component
                    entities_to_remove.append(key_entity)
                    logger.debug(
                        f"Removed duplicate key_entity '{qname}' from {component.name} (kept in {original_component.name})"
                    )
            else:
                seen_entities[qname] = component

        component.key_entities = [e for e in component.key_entities if e not in entities_to_remove]


def repair_unique_key_entities(result: ComponentRepairTarget, context: ComponentRepairContext) -> None:
    """Repair-chain form of ``ensure_unique_key_entities``.

    Runs again post-resolution in the agents' pipelines, where resolved
    reference files can settle which component keeps a contested entity.
    """
    ensure_unique_key_entities(result)


def repair_key_entities(result: ComponentRepairTarget, context: ComponentRepairContext) -> None:
    """Resolve key entities and remove references outside the current scope."""
    nodes_in_scope = {
        qualified_name
        for cluster_result in context.cluster_results.values()
        for members in cluster_result.clusters.values()
        for qualified_name in members
    }
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
