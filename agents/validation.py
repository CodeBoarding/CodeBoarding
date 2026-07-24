"""Validation utilities for LLM agent outputs."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    Component,
    ComponentFiles,
    Relation,
    ScopeOperationAction,
    ScopeRelations,
    ScopeUpdateDecision,
)
from agents.scope_operations import EXISTING_COMPONENT_ACTIONS, cluster_ref_from_scoped_ref
from diagram_analysis.cluster_delta import ClusterRef
from repo_utils import normalize_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.reference_resolver import StaticReferenceResolver

logger = logging.getLogger(__name__)

# Validator weight configuration.
# Coverage and relation evidence validators are the most critical structural
# checks. Key-entity validation is secondary (auto-correctable, less structural).
VALIDATOR_WEIGHTS: dict[str, float] = {
    "validate_group_name_coverage": 20.0,
    "validate_key_entities": 5.0,
    "validate_relations": 30.0,
    "validate_relation_component_names": 5.0,
    "validate_relation_evidence": 10.0,
    "validate_file_classifications": 5.0,
}
DEFAULT_VALIDATOR_WEIGHT = 5.0


@dataclass
class ValidationContext:
    """
    This class is used to provide the necessary context for validating different LLM steps.
    It encapsulates all relevant information required by validation routines to ensure that each step in the LLM pipeline
    is checked against the expected criteria.
    """

    cluster_results: dict[str, ClusterResult] = field(default_factory=dict)
    cfg_graphs: dict[str, CallGraph] = field(default_factory=dict)  # For edge checking
    expected_files: set[str] = field(default_factory=set)
    valid_component_names: set[str] = field(default_factory=set)  # For file classification validation
    existing_component_ids: set[str] = field(default_factory=set)  # For incremental ID-based routing validation
    repo_dir: str | None = None  # For path normalization
    static_analysis: StaticAnalysisResults | None = None  # For qualified name validation
    llm_cluster_analysis: ClusterAnalysis | None = None  # For group name coverage validation
    components: list[Component] = field(default_factory=list)  # For relation-only validation steps


@dataclass
class ScopeOperationValidationContext:
    expected_cluster_refs: set[ClusterRef] = field(default_factory=set)
    existing_component_ids: set[str] = field(default_factory=set)
    component_ids_by_cluster_ref: dict[ClusterRef, str] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    feedback_messages: list[str] = field(default_factory=list)
    score: float = 0.0


class RelationValidationTarget(Protocol):
    components_relations: list[Relation]


class ComponentValidationTarget(Protocol):
    components: list[Component]


def validate_scope_update_decision(
    decision: ScopeUpdateDecision,
    context: ScopeOperationValidationContext,
) -> ValidationResult:
    errors: list[str] = []
    claimed_refs: list[ClusterRef] = []  # every op's refs, for the over-claim (extra) and duplicate checks
    covered_refs: list[ClusterRef] = []  # non-delete refs only; a delete discards its clusters, so it cannot cover them
    for operation in decision.operations:
        refs = [cluster_ref_from_scoped_ref(ref) for ref in operation.cluster_refs]
        claimed_refs.extend(refs)
        if operation.action != ScopeOperationAction.DELETE_COMPONENT:
            covered_refs.extend(refs)
        if operation.action == ScopeOperationAction.NOOP:
            # A noop unions its refs into its component without removing them from the real owner,
            # so any changed cluster it claims from another component becomes duplicate-owned.
            foreign = {
                ref
                for ref in refs
                if context.component_ids_by_cluster_ref.get(ref) not in (None, operation.component_id)
            }
            if foreign:
                errors.append(
                    f"noop for component_id={operation.component_id!r} claims clusters owned by another "
                    f"component: {_format_cluster_ref_list(foreign)}. A noop must only preserve its own clusters."
                )
        if operation.action in EXISTING_COMPONENT_ACTIONS:
            if operation.component_id not in context.existing_component_ids:
                errors.append(
                    f"Operation {operation.action} references unknown component_id={operation.component_id!r}."
                )
        if operation.action == ScopeOperationAction.CREATE_COMPONENT:
            if not operation.name or not operation.description:
                errors.append("create_component operations must include name and description.")
        if operation.action == ScopeOperationAction.NOOP and (
            operation.name or operation.description or operation.key_entities
        ):
            errors.append("noop operations must preserve the component name, description, and key entities.")
        if len(operation.key_entities) > 5:
            errors.append(f"Operation {operation.action} includes more than five key entities.")
        entity_qnames = [entity.qualified_name for entity in operation.key_entities]
        duplicate_qnames = {qname for qname in entity_qnames if entity_qnames.count(qname) > 1}
        if duplicate_qnames:
            errors.append(f"Operation {operation.action} repeats key entities: {sorted(duplicate_qnames)}.")

    claimed_set: set[ClusterRef] = set()
    duplicates: set[ClusterRef] = set()
    for ref in claimed_refs:
        if ref in claimed_set:
            duplicates.add(ref)
        else:
            claimed_set.add(ref)
    missing = context.expected_cluster_refs - set(covered_refs)
    extra = claimed_set - context.expected_cluster_refs
    if missing:
        errors.append(f"Missing cluster_refs: {_format_cluster_ref_list(missing)}")
    if extra:
        errors.append(f"Unexpected cluster_refs: {_format_cluster_ref_list(extra)}")
    if duplicates:
        errors.append(f"Duplicate cluster_refs: {_format_cluster_ref_list(duplicates)}")
    return ValidationResult(is_valid=not errors, feedback_messages=errors)


def _format_cluster_ref_list(refs: set[ClusterRef]) -> str:
    sorted_refs = sorted(refs, key=lambda ref: (ref.scope_id, ref.language, ref.cluster_id))
    return ", ".join(f"{ref.scope_id}:{ref.language}:{ref.cluster_id}" for ref in sorted_refs) or "None"


def _effective_validation_score(result: ValidationResult) -> float:
    """Return a normalized score where a passing validator counts as complete."""
    if result.is_valid:
        return 1.0
    return max(0.0, min(1.0, result.score))


def score_validation_results(
    validator_results: list[tuple[Callable, ValidationResult]],
) -> float:
    """Compute a weighted score for a set of validation results.

    Each validator contributes its weight to the total score when it passes.
    The returned score is in the range [0, max_possible_score] where a higher
    value means a better result.

    Args:
        validator_results: List of (validator_function, ValidationResult) pairs.

    Returns:
        Weighted score (higher is better).
    """
    score = 0.0
    for validator_fn, vr in validator_results:
        weight = VALIDATOR_WEIGHTS.get(validator_fn.__name__, DEFAULT_VALIDATOR_WEIGHT)
        score += weight * _effective_validation_score(vr)
    return score


def validate_group_name_coverage(result: ComponentValidationTarget, context: ValidationContext) -> ValidationResult:
    """
    Validate bidirectional coverage between cluster groups and components:
    1. Every ClusterComponent must be referenced by at least one Component's source_group_names.
    2. Every Component must have at least one source_group_name assigned.
    3. Every source_group_name referenced by a Component must exist in the cluster analysis.

    Args:
        result: AnalysisInsights containing components with source_group_names
        context: ValidationContext with cluster_analysis

    Returns:
        ValidationResult with targeted feedback depending on the issue
    """
    if not context.llm_cluster_analysis:
        logger.warning("[Validation] No cluster_analysis provided for group name coverage validation")
        return ValidationResult(is_valid=True)

    expected_group_names = {cc.name for cc in context.llm_cluster_analysis.cluster_components}

    referenced_group_names: set[str] = set()
    for component in result.components:
        referenced_group_names.update(component.source_group_names)

    # Check 1: Cluster groups not referenced by any component (case-insensitive)
    expected_lower = {name.lower(): name for name in expected_group_names}
    referenced_lower = {name.lower() for name in referenced_group_names}
    missing_groups = {original for lower, original in expected_lower.items() if lower not in referenced_lower}

    # Check 2: Components without any source_group_names
    empty_components = [comp.name for comp in result.components if not comp.source_group_names]

    # Check 3: Components referencing non-existent group names (typos/hallucinations)
    unknown_refs: dict[str, list[str]] = {}
    for comp in result.components:
        for gname in comp.source_group_names:
            if gname.lower() not in expected_lower:
                unknown_refs.setdefault(comp.name, []).append(gname)

    if not missing_groups and not empty_components and not unknown_refs:
        logger.info("[Validation] All cluster groups and components have proper bidirectional coverage")
        return ValidationResult(is_valid=True)

    feedback_messages: list[str] = []

    if missing_groups and not empty_components:
        missing_str = ", ".join(sorted(missing_groups))
        feedback_messages.append(
            f"The following cluster groups are not assigned to any component: {missing_str}. "
            f"Please revisit whether they were missed or whether they require a new component of their own."
        )
        logger.warning(f"[Validation] Unassigned cluster groups: {missing_str}")

    if empty_components and not missing_groups:
        empty_str = ", ".join(sorted(empty_components))
        feedback_messages.append(
            f"The following components have no source_group_names assigned: {empty_str}. "
            f"All cluster groups are already covered, so these components have no source code backing them. "
            f"Please revisit the component structure — consider removing these components or "
            f"redistributing cluster groups to include them."
        )
        logger.warning(f"[Validation] Components without source groups: {empty_str}")

    if missing_groups and empty_components:
        missing_str = ", ".join(sorted(missing_groups))
        empty_str = ", ".join(sorted(empty_components))
        all_names_str = ", ".join(sorted(expected_group_names))
        feedback_messages.append(
            f"Cluster groups not assigned to any component: {missing_str}. "
            f"Components without any source_group_names: {empty_str}. "
            f"All available cluster group names are: {all_names_str}. "
            f"Please ensure every cluster group is assigned to a component via source_group_names "
            f"and every component references at least one cluster group."
        )
        logger.warning(f"[Validation] Unassigned groups: {missing_str}; empty components: {empty_str}")

    if unknown_refs:
        details = "; ".join(f"{comp}: {', '.join(names)}" for comp, names in sorted(unknown_refs.items()))
        all_names_str = ", ".join(sorted(expected_group_names))
        feedback_messages.append(
            f"The following components reference non-existent cluster group names: {details}. "
            f"Available cluster group names are: {all_names_str}. "
            f"Please fix or remove the invalid source_group_names."
        )
        logger.warning(f"[Validation] Unknown source_group_names: {details}")

    return ValidationResult(is_valid=False, feedback_messages=feedback_messages)


def validate_key_entities(result: ComponentValidationTarget, context: ValidationContext) -> ValidationResult:
    """Validate that every component retains at least one repaired key entity."""
    empty_components = [c.name for c in result.components if not c.key_entities]
    if empty_components:
        missing_str = ", ".join(empty_components)
        logger.warning(f"[Validation] Components with no valid key entities: {missing_str}")
        return ValidationResult(
            is_valid=False,
            feedback_messages=[
                f"The following components have no valid key entities: {missing_str}. "
                f"Every component must have at least one key entity (critical class or method) "
                f"that represents its core functionality. Use exact qualified names from the cluster analysis."
            ],
        )

    logger.info("[Validation] All key entities are valid")
    return ValidationResult(is_valid=True)


def validate_file_classifications(result: ComponentFiles, context: ValidationContext) -> ValidationResult:
    """
    Validate that all unassigned files were classified to valid component names.

    This validator is used for _classify_unassigned_files_with_llm to ensure:
    1. All input files are present in the result
    2. All component names are valid (exist in valid_component_names)

    Args:
        result: ComponentFiles with file_paths containing FileClassification objects
        context: ValidationContext with expected_files (unassigned files) and valid_component_names

    Returns:
        ValidationResult with feedback for missing files or invalid component names
    """
    if not context.expected_files:
        logger.warning("[Validation] No expected files provided for file classification validation")
        return ValidationResult(is_valid=True)

    feedback_messages = []

    # Get classified file paths from result
    classified_files = {normalize_path(fc.file_path, context.repo_dir) for fc in result.file_paths}

    # Normalize paths for comparison
    expected_files_normalized = {normalize_path(file_path, context.repo_dir) for file_path in context.expected_files}

    # Check 1: Are all unassigned files classified?
    missing_files = expected_files_normalized - classified_files
    if missing_files:
        missing_list = sorted(str(f) for f in missing_files)[:10]
        missing_str = ", ".join(missing_list)
        more_msg = f" and {len(missing_files) - 10} more" if len(missing_files) > 10 else ""
        feedback_messages.append(
            f"The following files were not classified: {missing_str}{more_msg}. "
            f"Please ensure all files are assigned to a component."
        )

    # Check 2: Are all component names valid?
    if context.valid_component_names:
        invalid_classifications = []
        for fc in result.file_paths:
            if fc.component_name not in context.valid_component_names:
                invalid_classifications.append(f"{fc.file_path} -> {fc.component_name}")

        if invalid_classifications:
            invalid_str = ", ".join(invalid_classifications[:10])
            more_msg = f" and {len(invalid_classifications) - 10} more" if len(invalid_classifications) > 10 else ""
            valid_names = ", ".join(sorted(context.valid_component_names))
            feedback_messages.append(
                f"Invalid component names found: {invalid_str}{more_msg}. "
                f"Valid component names are: {valid_names}. "
                f"Please use only these component names."
            )

    if not feedback_messages:
        logger.info("[Validation] All unassigned files correctly classified")
        return ValidationResult(is_valid=True)

    logger.warning(f"[Validation] File classification issues: {len(feedback_messages)} problems found")
    return ValidationResult(is_valid=False, feedback_messages=feedback_messages)


def validate_relation_component_names(
    result: RelationValidationTarget, _context: ValidationContext
) -> ValidationResult:
    """
    Validate that every src_name and dst_name in components_relations refers to an existing component.

    When a relation references a component name that does not exist, assign_component_ids will
    leave src_id or dst_id as an empty string, producing broken references in the output JSON.

    Args:
        result: AnalysisInsights containing components and components_relations
        context: ValidationContext (not used but kept for interface consistency)

    Returns:
        ValidationResult with feedback listing every relation whose src_name or dst_name is unknown
    """
    components = result.components if hasattr(result, "components") else _context.components
    known_names = {component.name for component in components}

    invalid_relations: list[str] = []
    total_relations = len(result.components_relations)
    for relation in result.components_relations:
        unknown: list[str] = []
        if relation.src_name not in known_names:
            unknown.append(f"src_name='{relation.src_name}'")
        if relation.dst_name not in known_names:
            unknown.append(f"dst_name='{relation.dst_name}'")
        if unknown:
            invalid_relations.append(
                f"({relation.src_name} -{relation.relation}-> {relation.dst_name}): {', '.join(unknown)}"
            )

    if not invalid_relations:
        logger.info("[Validation] All relation component names refer to existing components")
        return ValidationResult(is_valid=True)

    invalid_str = "; ".join(invalid_relations)
    known_str = ", ".join(sorted(known_names)) if known_names else "<none>"
    feedback = (
        f"The following relations reference component names that do not exist: {invalid_str}. "
        f"Known component names are: {known_str}. "
        f"Please ensure that src_name and dst_name in every relation match an existing component name exactly."
    )

    logger.warning(f"[Validation] Relations with unknown component names: {invalid_str}")
    valid_count = max(0, total_relations - len(invalid_relations))
    return ValidationResult(
        is_valid=False,
        feedback_messages=[feedback],
        score=(valid_count / total_relations) if total_relations else 0.0,
    )


def validate_relation_evidence(result: RelationValidationTarget, context: ValidationContext) -> ValidationResult:
    """Validate LLM relations against directed CFG edges or explicit runtime evidence."""
    if not context.llm_cluster_analysis or not context.cluster_results or not context.cfg_graphs:
        logger.warning("[Validation] Missing static context for relation evidence validation")
        return ValidationResult(is_valid=True)

    components = context.components
    if not components and isinstance(result, AnalysisInsights):
        components = result.components
    component_to_clusters = _component_cluster_ids(components, context.llm_cluster_analysis)
    cluster_edge_lookup = _build_cluster_edge_lookup(context.cluster_results, context.cfg_graphs)
    unsupported: list[str] = []
    invalid_key_edges: list[str] = []
    unresolved_key_edges: list[str] = []
    valid_relation_count = 0
    total_relations = len(result.components_relations)

    for relation in result.components_relations:
        src_clusters = component_to_clusters.get(relation.src_name, [])
        dst_clusters = component_to_clusters.get(relation.dst_name, [])
        valid_key_edges, same_endpoint_edges, unresolved_edges = _valid_key_edge_descriptions(relation, context)
        if same_endpoint_edges:
            invalid_key_edges.append(f"{relation.src_name} -> {relation.dst_name}: {', '.join(same_endpoint_edges)}")
        if unresolved_edges:
            unresolved_key_edges.append(f"{relation.src_name} -> {relation.dst_name}: {', '.join(unresolved_edges)}")
        if _cluster_sets_have_edge(src_clusters, dst_clusters, cluster_edge_lookup):
            if not same_endpoint_edges and not unresolved_edges:
                valid_relation_count += 1
            continue
        if unresolved_edges:
            continue
        if valid_key_edges:
            valid_relation_count += 1
            continue
        if _has_relation_evidence(relation) and not relation.key_edges:
            valid_relation_count += 1
            continue
        unsupported.append(f"{relation.src_name} -> {relation.dst_name}: {relation.relation}")

    feedback_messages: list[str] = []
    if unsupported:
        feedback_messages.append(
            "The following relations have no directed static CFG edge between their components, and any supplied "
            "key_edges/evidence could not support the relation: "
            f"{'; '.join(unsupported)}. Remove them, or keep only if they represent a real runtime/non-static "
            "interaction and add key_edges or relation evidence with a concise concrete reason "
            "(for example an endpoint, queue/topic, plugin registration, subprocess, reflection/import hook, or "
            "config-driven wiring)."
        )
    if invalid_key_edges:
        feedback_messages.append(
            "The following relation key_edges resolve source and target to the same method/symbol: "
            f"{'; '.join(invalid_key_edges)}. Replace each with a real bridge edge whose source is the calling, "
            "registering, routing, or configuring code and whose target is the distinct invoked/registered/routed endpoint."
        )
    if unresolved_key_edges:
        feedback_messages.append(
            "The following relation key_edges could not be resolved to known source symbols and look hallucinated: "
            f"{'; '.join(unresolved_key_edges)}. Drop them, or replace them with exact existing symbols from the code. "
            "If the relation is runtime/config/plugin-driven and has no direct code edge, use relation-level evidence instead."
        )

    if not feedback_messages:
        logger.info("[Validation] All relations have static backing or explicit evidence")
        return ValidationResult(is_valid=True)

    if unsupported:
        logger.warning("[Validation] Relations without static backing or evidence: %s", "; ".join(unsupported))
    if invalid_key_edges:
        logger.warning("[Validation] Relations with degenerate key edges: %s", "; ".join(invalid_key_edges))
    if unresolved_key_edges:
        logger.warning("[Validation] Relations with unresolved key edges: %s", "; ".join(unresolved_key_edges))
    return ValidationResult(
        is_valid=False,
        feedback_messages=feedback_messages,
        score=(valid_relation_count / total_relations) if total_relations else 0.0,
    )


def validate_relations(result: RelationValidationTarget, context: ValidationContext) -> ValidationResult:
    """Validate relation endpoints and edge evidence as one structural check."""
    name_result = validate_relation_component_names(result, context)
    evidence_result = validate_relation_evidence(result, context)

    if name_result.is_valid and evidence_result.is_valid:
        return ValidationResult(is_valid=True)

    feedback_parts: list[str] = []
    if not name_result.is_valid:
        feedback_parts.extend(name_result.feedback_messages)
    if not evidence_result.is_valid:
        feedback_parts.extend(evidence_result.feedback_messages)

    return ValidationResult(
        is_valid=False,
        feedback_messages=[
            "Fix the component relations as a single edge set: every relation must use exact existing component "
            "names on both sides, and every relation must be backed by a directed CFG edge or resolvable key_edges. "
            + " ".join(feedback_parts)
        ],
        score=min(_effective_validation_score(name_result), _effective_validation_score(evidence_result)),
    )


def _valid_key_edge_descriptions(relation, context: ValidationContext) -> tuple[list[str], list[str], list[str]]:
    """Return valid, same-endpoint, and unresolved key edge descriptions."""
    if context.static_analysis is None:
        return ([edge.llm_str() for edge in relation.key_edges], [], [])
    resolver = StaticReferenceResolver(Path(context.repo_dir or "."), context.static_analysis)
    valid: list[str] = []
    same_endpoint: list[str] = []
    unresolved: list[str] = []
    for edge in relation.key_edges:
        resolution = resolver.classify_key_edge(edge, context.cfg_graphs)
        if resolution.valid:
            valid.append(resolution.description)
            continue
        if resolution.same_endpoint:
            same_endpoint.append(resolution.description)
            continue
        if resolution.unresolved:
            unresolved.append(resolution.description)
    return valid, same_endpoint, unresolved


def _has_relation_evidence(relation) -> bool:
    """Return true when an LLM-only relation has concrete textual evidence."""
    if relation.evidence.strip():
        return True
    return any(edge.description.strip() for edge in relation.key_edges)


def validate_scope_relation_names(result: ScopeRelations, _context: ValidationContext) -> ValidationResult:
    """Validate that src_name/dst_name in scope relations match known component names."""
    known_names = _context.valid_component_names
    if not known_names:
        return ValidationResult(is_valid=True)

    invalid: list[str] = []
    for rel in result.components_relations:
        unknown: list[str] = []
        if rel.src_name not in known_names:
            unknown.append(f"src_name='{rel.src_name}'")
        if rel.dst_name not in known_names:
            unknown.append(f"dst_name='{rel.dst_name}'")
        if unknown:
            invalid.append(f"({rel.src_name} -{rel.relation}-> {rel.dst_name}): {', '.join(unknown)}")

    if not invalid:
        return ValidationResult(is_valid=True)

    known_str = ", ".join(sorted(known_names))
    feedback = (
        f"The following relations reference component names that do not exist: {'; '.join(invalid)}. "
        f"Known component names are: {known_str}. "
        f"Ensure src_name and dst_name match an existing component name exactly."
    )
    logger.warning(f"[Validation] Scope relations with unknown names: {'; '.join(invalid)}")
    return ValidationResult(is_valid=False, feedback_messages=[feedback])


def _build_cluster_edge_lookup(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, CallGraph],
) -> dict[str, set[tuple[int, int]]]:
    """Build a lookup of (src_cluster_id, dst_cluster_id) edges per language."""
    cluster_edge_lookup: dict[str, set[tuple[int, int]]] = {}

    for lang, cfg in cfg_graphs.items():
        cluster_result = cluster_results.get(lang)
        if not cluster_result:
            continue

        node_to_cluster: dict[str, int] = {}
        for cluster_id, nodes in cluster_result.clusters.items():
            for node in nodes:
                node_to_cluster[node] = cluster_id

        cluster_edges: set[tuple[int, int]] = set()
        for edge in cfg.edges:
            src_cluster = node_to_cluster.get(edge.get_source())
            dst_cluster = node_to_cluster.get(edge.get_destination())
            if src_cluster is None or dst_cluster is None:
                continue
            cluster_edges.add((src_cluster, dst_cluster))

        cluster_edge_lookup[lang] = cluster_edges

    return cluster_edge_lookup


def _component_cluster_ids(components: list[Component], cluster_analysis: ClusterAnalysis) -> dict[str, list[int]]:
    group_to_cluster_ids = {group.name: group.cluster_ids for group in cluster_analysis.cluster_components}
    component_to_clusters: dict[str, list[int]] = {}
    for component in components:
        cluster_ids: list[int] = []
        for group_name in component.source_group_names:
            cluster_ids.extend(group_to_cluster_ids.get(group_name, []))
        component_to_clusters[component.name] = cluster_ids
    return component_to_clusters


def _cluster_sets_have_edge(
    src_cluster_ids: list[int],
    dst_cluster_ids: list[int],
    cluster_edge_lookup: dict[str, set[tuple[int, int]]],
) -> bool:
    if not src_cluster_ids or not dst_cluster_ids:
        return False

    src_set = set(src_cluster_ids)
    dst_set = set(dst_cluster_ids)
    for cluster_edges in cluster_edge_lookup.values():
        for src_cluster, dst_cluster in cluster_edges:
            if src_cluster in src_set and dst_cluster in dst_set:
                return True
    return False


def _check_edge_between_cluster_sets(
    src_cluster_ids: list[int],
    dst_cluster_ids: list[int],
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, CallGraph],
    cluster_edge_lookup: dict[str, set[tuple[int, int]]] | None = None,
) -> bool:
    """
    Check if there's an edge between any pair of clusters from two sets.

    Args:
        src_cluster_ids: Source cluster IDs
        dst_cluster_ids: Destination cluster IDs
        cluster_results: dict mapping language -> ClusterResult
        cfg_graphs: dict mapping language -> CallGraph
        cluster_edge_lookup: Optional precomputed (src_cluster, dst_cluster) edges per language

    Returns:
        True if any edge exists between the cluster sets
    """
    if not src_cluster_ids or not dst_cluster_ids:
        return False

    if cluster_edge_lookup is None:
        cluster_edge_lookup = _build_cluster_edge_lookup(cluster_results, cfg_graphs)

    return _cluster_sets_have_edge(src_cluster_ids, dst_cluster_ids, cluster_edge_lookup) or _cluster_sets_have_edge(
        dst_cluster_ids, src_cluster_ids, cluster_edge_lookup
    )
