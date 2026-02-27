"""Validation utilities for LLM agent outputs."""

import logging
from dataclasses import dataclass, field

from agents.agent_responses import AnalysisInsights, ClusterAnalysis, ComponentFiles
from repo_utils import normalize_path
from static_analyzer.graph import CallGraph, ClusterResult

from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


@dataclass
class ValidationContext:
    """
    This class is used to provide the necessary context for validating different LLM steps.
    It encapsulates all relevant information required by validation routines to ensure that each step in the LLM pipeline
    is checked against the expected criteria.
    """

    cluster_results: dict[str, ClusterResult] = field(default_factory=dict)
    cfg_graphs: dict[str, CallGraph] = field(default_factory=dict)  # For edge checking
    expected_cluster_ids: set[int] = field(default_factory=set)
    expected_files: set[str] = field(default_factory=set)
    valid_component_names: set[str] = field(default_factory=set)  # For file classification validation
    repo_dir: str | None = None  # For path normalization
    static_analysis: StaticAnalysisResults | None = None  # For qualified name validation


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    feedback_messages: list[str] = field(default_factory=list)


def validate_cluster_coverage(
    result: ClusterAnalysis | AnalysisInsights, context: ValidationContext
) -> ValidationResult:
    """
    Validate that all expected clusters are represented in the result.

    Handles both ClusterAnalysis (cluster_components) and AnalysisInsights (components with source_cluster_ids).

    Args:
        result: ClusterAnalysis or AnalysisInsights
        context: ValidationContext with expected_cluster_ids

    Returns:
        ValidationResult with feedback for missing clusters
    """
    if not context.expected_cluster_ids:
        logger.warning("[Validation] No expected cluster IDs provided for coverage validation")
        return ValidationResult(is_valid=True)

    # Extract all cluster IDs from the result
    result_cluster_ids: set[int] = set()
    if isinstance(result, ClusterAnalysis):
        for component in result.cluster_components:
            result_cluster_ids.update(component.cluster_ids)
    if isinstance(result, AnalysisInsights):
        for component in result.components:
            result_cluster_ids.update(component.source_cluster_ids)

    # Find missing clusters
    missing_clusters = context.expected_cluster_ids - result_cluster_ids

    if not missing_clusters:
        logger.info("[Validation] All clusters are represented in the ClusterAnalysis")
        return ValidationResult(is_valid=True)

    # Build feedback message
    missing_str = ", ".join(str(cid) for cid in sorted(missing_clusters))
    feedback = (
        f"The following cluster IDs are missing from the analysis: {missing_str}. "
        f"Please ensure all clusters are assigned to a component via source_cluster_ids."
    )

    logger.warning(f"[Validation] Missing clusters: {missing_str}")
    return ValidationResult(is_valid=False, feedback_messages=[feedback])


def validate_component_relationships(result: AnalysisInsights, context: ValidationContext) -> ValidationResult:
    """
    Validate that component relationships have corresponding edges in the cluster graph.

    Args:
        result: AnalysisInsights containing components and components_relations
        context: ValidationContext with cluster_results and cfg_graphs

    Returns:
        ValidationResult with feedback for invalid relationships
    """
    if not context.cfg_graphs or not result.components_relations:
        logger.warning("[Validation] No CFG graphs or component relationships provided for relationship validation")
        return ValidationResult(is_valid=True)

    # Build component name -> source_cluster_ids mapping
    component_clusters: dict[str, list[int]] = {}
    for component in result.components:
        component_clusters[component.name] = component.source_cluster_ids

    cluster_edge_lookup = _build_cluster_edge_lookup(context.cluster_results, context.cfg_graphs)

    invalid_relations: list[str] = []

    for relation in result.components_relations:
        src_clusters = component_clusters.get(relation.src_name, [])
        dst_clusters = component_clusters.get(relation.dst_name, [])

        if not src_clusters or not dst_clusters:
            continue

        # Check if any cluster pair has an edge
        has_edge = _check_edge_between_cluster_sets(
            src_clusters,
            dst_clusters,
            context.cluster_results,
            context.cfg_graphs,
            cluster_edge_lookup,
        )

        if not has_edge:
            invalid_relations.append(f"({relation.src_name} -> {relation.dst_name})")

    if not invalid_relations:
        logger.info("[Validation] All component relationships have backing edges")
        return ValidationResult(is_valid=True)

    # Build feedback message
    invalid_str = ", ".join(invalid_relations)
    feedback = (
        f"The following component relationships lack backing edges in the cluster graph: {invalid_str}. "
        f"Please double-check if these components are actually related. If there is no direct edge between "
        f"their clusters, the relationship may be indirect or incorrect."
    )

    logger.warning(f"[Validation] Invalid relationships: {invalid_str}")
    return ValidationResult(is_valid=False, feedback_messages=[feedback])


def validate_key_entities(result: AnalysisInsights, context: ValidationContext) -> ValidationResult:
    """
    Validate that every component in AnalysisInsights has at least one key_entity assigned.

    Args:
        result: AnalysisInsights containing components
        context: ValidationContext (not used but kept for interface consistency)

    Returns:
        ValidationResult with feedback for components missing key entities
    """
    components_without_key_entities: list[str] = []

    for component in result.components:
        if not component.key_entities or len(component.key_entities) == 0:
            components_without_key_entities.append(component.name)

    if not components_without_key_entities:
        logger.info("[Validation] All components have key entities assigned")
        return ValidationResult(is_valid=True)

    # Build feedback message
    missing_str = ", ".join(components_without_key_entities)
    feedback = (
        f"The following components are missing key entities: {missing_str}. "
        f"Every component must have at least one key entity (critical class or method) "
        f"that represents its core functionality. Please identify and add 2-5 key entities "
        f"for each component."
    )

    logger.warning(f"[Validation] Components without key entities: {missing_str}")
    return ValidationResult(is_valid=False, feedback_messages=[feedback])


def validate_cluster_ids_populated(result: AnalysisInsights, context: ValidationContext) -> ValidationResult:
    """
    Validate that every cluster is assigned to at least one component.

    Args:
        result: AnalysisInsights containing components
        context: ValidationContext with cluster_results to get available cluster IDs

    Returns:
        ValidationResult with feedback for unassigned clusters
    """
    if not context.cluster_results:
        logger.warning("[Validation] No cluster results provided for cluster ID validation")
        return ValidationResult(is_valid=True)

    all_cluster_ids: set[int] = set()
    for lang_result in context.cluster_results.values():
        all_cluster_ids.update(lang_result.get_cluster_ids())

    if not all_cluster_ids:
        logger.warning("[Validation] No cluster IDs available for cluster ID validation")
        return ValidationResult(is_valid=True)

    assigned_cluster_ids: set[int] = set()
    for component in result.components:
        assigned_cluster_ids.update(component.source_cluster_ids or [])

    unassigned_clusters = all_cluster_ids - assigned_cluster_ids

    if not unassigned_clusters:
        logger.info("[Validation] All clusters are assigned to components")
        return ValidationResult(is_valid=True)

    missing_str = ", ".join(str(cid) for cid in sorted(unassigned_clusters))
    feedback = (
        f"The following cluster IDs are not assigned to any component: {missing_str}. "
        f"Please assign every cluster to a component based on which code clusters belong to it."
    )

    logger.warning(f"[Validation] Unassigned clusters: {missing_str}")
    return ValidationResult(is_valid=False, feedback_messages=[feedback])


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


def validate_relation_component_names(result: AnalysisInsights, _context: ValidationContext) -> ValidationResult:
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
    known_names = {component.name for component in result.components}

    invalid_relations: list[str] = []
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
    return ValidationResult(is_valid=False, feedback_messages=[feedback])


def validate_qualified_names(result: AnalysisInsights, context: ValidationContext) -> ValidationResult:
    """
    Validate that qualified names in key_entities exist in static analysis references.

    Args:
        result: AnalysisInsights containing components with key_entities
        context: ValidationContext with static_analysis to check references

    Returns:
        ValidationResult with feedback for invalid qualified names
    """
    if not context.static_analysis:
        logger.warning("[Validation] No static analysis provided for qualified name validation")
        return ValidationResult(is_valid=True)

    invalid_references: list[str] = []
    for component in result.components:
        for key_entity in component.key_entities:
            qname = key_entity.qualified_name.replace("/", ".")
            found = False

            # Check if qualified name exists in any language
            for lang in context.static_analysis.get_languages():
                try:
                    context.static_analysis.get_reference(lang, qname)
                    found = True
                    break
                except (ValueError, FileExistsError):
                    continue

            if not found:
                invalid_references.append(f"{component.name}: '{key_entity.qualified_name}'")

    if not invalid_references:
        logger.info("[Validation] All qualified names exist in static analysis references")
        return ValidationResult(is_valid=True)

    invalid_str = "; ".join(invalid_references[:10])
    more_msg = f" and {len(invalid_references) - 10} more" if len(invalid_references) > 10 else ""
    feedback = (
        f"The following qualified names do not exist in the static analysis: {invalid_str}{more_msg}. "
        f"Please ensure all key_entities use qualified names that were found during static analysis."
    )

    logger.warning(f"[Validation] Invalid qualified names: {len(invalid_references)} found")
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

    src_set = set(src_cluster_ids)
    dst_set = set(dst_cluster_ids)

    for cluster_edges in cluster_edge_lookup.values():
        for src_cluster, dst_cluster in cluster_edges:
            if src_cluster in src_set and dst_cluster in dst_set:
                return True

    return False
