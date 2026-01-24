"""Validation utilities for LLM agent outputs."""

import logging
import os
from dataclasses import dataclass, field

from agents.agent_responses import ClusterAnalysis, AnalysisInsights, ComponentFiles
from static_analyzer.graph import ClusterResult, CallGraph

logger = logging.getLogger(__name__)


@dataclass
class ValidationContext:
    """Context for validation functions. Contains all data needed for validation."""

    cluster_results: dict[str, ClusterResult] = field(default_factory=dict)
    cfg_graphs: dict[str, CallGraph] = field(default_factory=dict)  # For edge checking
    expected_cluster_ids: set[int] = field(default_factory=set)
    expected_files: set[str] = field(default_factory=set)
    valid_component_names: set[str] = field(default_factory=set)  # For file classification validation
    repo_dir: str | None = None  # For path normalization


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    feedback_messages: list[str] = field(default_factory=list)


def validate_cluster_coverage(result: ClusterAnalysis, context: ValidationContext) -> ValidationResult:
    """
    Validate that all expected clusters are represented in the ClusterAnalysis.

    Args:
        result: ClusterAnalysis containing cluster_components
        context: ValidationContext with expected_cluster_ids

    Returns:
        ValidationResult with feedback for missing clusters
    """
    if not context.expected_cluster_ids:
        logger.warning("[Validation] No expected cluster IDs provided for coverage validation")
        return ValidationResult(is_valid=True)

    # Extract all cluster IDs from the result
    result_cluster_ids = set()
    for component in result.cluster_components:
        result_cluster_ids.update(component.cluster_ids)

    # Find missing clusters
    missing_clusters = context.expected_cluster_ids - result_cluster_ids

    if not missing_clusters:
        logger.info("[Validation] All clusters are represented in the ClusterAnalysis")
        return ValidationResult(is_valid=True)

    # Build feedback message
    missing_str = ", ".join(str(cid) for cid in sorted(missing_clusters))
    feedback = (
        f"The following cluster IDs are missing from the analysis: {missing_str}. "
        f"Please ensure all clusters are assigned to a component or create new components for them."
    )

    logger.warning(f"[Validation] Missing clusters: {missing_str}")
    return ValidationResult(is_valid=False, feedback_messages=[feedback])


def validate_component_relationships(result: AnalysisInsights, context: ValidationContext) -> ValidationResult:
    """
    Validate that component relationships have backing edges in the cluster graph.

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

    invalid_relations: list[str] = []

    for relation in result.components_relations:
        src_clusters = component_clusters.get(relation.src_name, [])
        dst_clusters = component_clusters.get(relation.dst_name, [])

        if not src_clusters or not dst_clusters:
            continue

        # Check if any cluster pair has an edge
        has_edge = _check_edge_between_cluster_sets(
            src_clusters, dst_clusters, context.cluster_results, context.cfg_graphs
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
    classified_files = {fc.file_path for fc in result.file_paths}

    # Normalize paths for comparison
    expected_files_normalized = set()
    for file_path in context.expected_files:
        if context.repo_dir and os.path.isabs(file_path):
            file_path = os.path.relpath(file_path, context.repo_dir)
        expected_files_normalized.add(file_path)

    # Check 1: Are all unassigned files classified?
    missing_files = expected_files_normalized - classified_files
    if missing_files:
        missing_list = sorted(missing_files)[:10]
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


def _check_edge_between_cluster_sets(
    src_cluster_ids: list[int],
    dst_cluster_ids: list[int],
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, CallGraph],
) -> bool:
    """
    Check if there's an edge between any pair of clusters from two sets.

    Args:
        src_cluster_ids: Source cluster IDs
        dst_cluster_ids: Destination cluster IDs
        cluster_results: dict mapping language -> ClusterResult
        cfg_graphs: dict mapping language -> CallGraph

    Returns:
        True if any edge exists between the cluster sets
    """
    # For each language, check if there's an edge
    for lang, cfg in cfg_graphs.items():
        cluster_result = cluster_results.get(lang)
        if not cluster_result:
            continue

        # Build node -> cluster mapping
        node_to_cluster: dict[str, int] = {}
        for cluster_id, nodes in cluster_result.clusters.items():
            for node in nodes:
                node_to_cluster[node] = cluster_id

        # Get networkx graph
        nx_graph = cfg.to_networkx()

        # Check edges
        for src_node, dst_node in nx_graph.edges():
            src_cluster = node_to_cluster.get(src_node)
            dst_cluster = node_to_cluster.get(dst_node)

            if src_cluster is None or dst_cluster is None:
                continue

            # Check if this edge connects our cluster sets
            if src_cluster in src_cluster_ids and dst_cluster in dst_cluster_ids:
                return True

    return False
