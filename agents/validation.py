"""Validation utilities for LLM agent outputs."""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from agents.agent_responses import AnalysisInsights, ClusterAnalysis, ComponentFiles
from repo_utils import normalize_path
from static_analyzer.graph import CallGraph, ClusterResult

from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)

# Validator weight configuration.
# Coverage validators (cluster + group name) are the most critical — they
# determine whether every cluster is accounted for and properly assigned.
# Key-entity validation is secondary (auto-correctable, less structural).
VALIDATOR_WEIGHTS: dict[str, float] = {
    "validate_cluster_coverage": 20.0,
    "validate_group_name_coverage": 20.0,
    "validate_key_entities": 5.0,
    "validate_relation_component_names": 5.0,
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
    expected_cluster_ids: set[int] = field(default_factory=set)
    expected_files: set[str] = field(default_factory=set)
    valid_component_names: set[str] = field(default_factory=set)  # For file classification validation
    repo_dir: str | None = None  # For path normalization
    static_analysis: StaticAnalysisResults | None = None  # For qualified name validation
    llm_cluster_analysis: ClusterAnalysis | None = None  # For group name coverage validation


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    feedback_messages: list[str] = field(default_factory=list)


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
        if vr.is_valid:
            score += weight
    return score


def validate_cluster_coverage(result: ClusterAnalysis, context: ValidationContext) -> ValidationResult:
    """
    Validate that all expected clusters are represented in the ClusterAnalysis
    and that no cluster component has an empty cluster_ids list.

    Args:
        result: ClusterAnalysis with cluster_components
        context: ValidationContext with expected_cluster_ids

    Returns:
        ValidationResult with feedback for missing clusters or empty components
    """
    if not context.expected_cluster_ids:
        logger.warning("[Validation] No expected cluster IDs provided for coverage validation")
        return ValidationResult(is_valid=True)

    feedback_messages: list[str] = []

    # Check for cluster components with empty cluster_ids
    empty_components = [cc.name for cc in result.cluster_components if not cc.cluster_ids]
    if empty_components:
        empty_str = ", ".join(sorted(empty_components))
        feedback_messages.append(
            f"The following cluster groups have no cluster IDs assigned: {empty_str}. "
            f"Every cluster group must reference at least one cluster ID. "
            f"Either assign clusters to these groups or remove them entirely."
        )
        logger.warning(f"[Validation] Cluster groups with empty cluster_ids: {empty_str}")

    # Check for duplicate cluster IDs across different cluster groups
    seen_clusters: dict[int, str] = {}
    duplicate_reports: list[str] = []
    for cc in result.cluster_components:
        for cid in cc.cluster_ids:
            if cid in seen_clusters:
                duplicate_reports.append(f"cluster {cid} in both '{seen_clusters[cid]}' and '{cc.name}'")
            else:
                seen_clusters[cid] = cc.name

    if duplicate_reports:
        dup_str = "; ".join(duplicate_reports)
        feedback_messages.append(
            f"The following cluster IDs are assigned to multiple groups: {dup_str}. "
            f"Each cluster ID must belong to exactly one group. "
            f"Please remove the duplicate assignments so every cluster is in only one group."
        )
        logger.warning(f"[Validation] Duplicate cluster assignments: {dup_str}")

    result_cluster_ids = set(seen_clusters.keys())

    missing_clusters = context.expected_cluster_ids - result_cluster_ids

    if missing_clusters:
        missing_str = ", ".join(str(cid) for cid in sorted(missing_clusters))
        feedback_messages.append(
            f"The following cluster IDs are missing from the analysis: {missing_str}. "
            f"Please ensure all clusters are assigned to a component via cluster_ids."
        )
        logger.warning(f"[Validation] Missing clusters: {missing_str}")

    if not feedback_messages:
        logger.info("[Validation] All clusters are represented in the ClusterAnalysis")
        return ValidationResult(is_valid=True)

    return ValidationResult(is_valid=False, feedback_messages=feedback_messages)


def _normalize_group_name(name: str) -> str:
    """Normalize a group name for fuzzy comparison: lowercase, collapse whitespace, strip punctuation."""
    name = name.lower().strip()
    name = re.sub(r"\s+", " ", name)
    # Remove common punctuation that LLMs might add/drop inconsistently
    name = re.sub(r"[()&/\\,\-–—]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _fuzzy_match_group_name(name: str, candidates: dict[str, str], threshold: float = 0.75) -> str | None:
    """Find the best fuzzy match for *name* among *candidates*.

    Args:
        name: The name to match (already normalized).
        candidates: Mapping of normalized_name -> original_name.
        threshold: Minimum similarity ratio (0-1) to accept a match.

    Returns:
        The original (canonical) name of the best match, or None.
    """
    best_score = 0.0
    best_match: str | None = None
    for norm_candidate, original in candidates.items():
        score = SequenceMatcher(None, name, norm_candidate).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_match = original
    return best_match


def _auto_correct_group_names(result: AnalysisInsights, expected_group_names: set[str]) -> int:
    """Auto-correct source_group_names on components in-place.

    Fixes case mismatches, whitespace differences, and close fuzzy matches
    (similar to how validate_key_entities auto-corrects key_entity names).

    Returns:
        Number of names that were auto-corrected.
    """
    # Build lookup: normalized_name -> canonical_name
    canonical_lookup: dict[str, str] = {_normalize_group_name(name): name for name in expected_group_names}

    auto_corrected = 0
    for component in result.components:
        corrected_names: list[str] = []
        for gname in component.source_group_names:
            normalized = _normalize_group_name(gname)

            # Exact normalized match (handles case + whitespace)
            if normalized in canonical_lookup:
                canonical = canonical_lookup[normalized]
                if gname != canonical:
                    logger.info(
                        f"[Validation] Auto-corrected source_group_name: "
                        f"'{gname}' -> '{canonical}' (component '{component.name}')"
                    )
                    auto_corrected += 1
                corrected_names.append(canonical)
                continue

            # Fuzzy match for close typos
            fuzzy_match = _fuzzy_match_group_name(normalized, canonical_lookup)
            if fuzzy_match is not None:
                logger.info(
                    f"[Validation] Auto-corrected source_group_name (fuzzy): "
                    f"'{gname}' -> '{fuzzy_match}' (component '{component.name}')"
                )
                auto_corrected += 1
                corrected_names.append(fuzzy_match)
                continue

            # No match found, keep original for error reporting
            corrected_names.append(gname)

        component.source_group_names = corrected_names

    return auto_corrected


def validate_group_name_coverage(result: AnalysisInsights, context: ValidationContext) -> ValidationResult:
    """
    Validate bidirectional coverage between cluster groups and components:
    1. Auto-correct trivial mismatches (case, whitespace, close typos) in-place.
    2. Every ClusterComponent must be referenced by at least one Component's source_group_names.
    3. Every Component must have at least one source_group_name assigned.
    4. Every source_group_name referenced by a Component must exist in the cluster analysis.

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

    # Auto-correct trivial mismatches before validation
    auto_corrected = _auto_correct_group_names(result, expected_group_names)
    if auto_corrected:
        logger.info(f"[Validation] Auto-corrected {auto_corrected} source_group_name(s)")

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


def validate_key_entities(result: AnalysisInsights, context: ValidationContext) -> ValidationResult:
    """
    Validate key_entities on every component:
    1. Auto-correct qualified names via loose matching.
    2. Silently drop invalid key entities (out of scope or not found).
    3. Only fail if a component ends up with zero key_entities after dropping.

    Args:
        result: AnalysisInsights containing components with key_entities
        context: ValidationContext with optional static_analysis for name resolution
                 and cluster_results for scope validation

    Returns:
        ValidationResult with feedback only for components left with zero key entities
    """
    auto_corrected = 0

    # Auto-correct qualified names via loose matching (always, when static_analysis available)
    if context.static_analysis:
        for component in result.components:
            for key_entity in component.key_entities:
                qname = key_entity.qualified_name.replace("/", ".")
                node = context.static_analysis.resolve_across_languages(qname)
                if node is not None and node.fully_qualified_name != qname:
                    logger.info(
                        f"[Validation] Auto-corrected qualified name: "
                        f"'{key_entity.qualified_name}' -> '{node.fully_qualified_name}'"
                    )
                    key_entity.qualified_name = node.fully_qualified_name
                    auto_corrected += 1
        if auto_corrected:
            logger.info(f"[Validation] Auto-corrected {auto_corrected} qualified names via loose matching")

    # Silently drop invalid key entities
    dropped = 0
    if context.cluster_results:
        nodes_in_scope: set[str] = set()
        for cr in context.cluster_results.values():
            for members in cr.clusters.values():
                nodes_in_scope.update(members)

        for component in result.components:
            valid = []
            for key_entity in component.key_entities:
                qname = key_entity.qualified_name
                in_scope = qname in nodes_in_scope
                if not in_scope:
                    for scope_node in nodes_in_scope:
                        if qname.startswith(scope_node + ".") or scope_node.startswith(qname + "."):
                            in_scope = True
                            break
                if in_scope:
                    valid.append(key_entity)
                else:
                    dropped += 1
            component.key_entities = valid

    elif context.static_analysis:
        for component in result.components:
            valid = []
            for key_entity in component.key_entities:
                qname = key_entity.qualified_name.replace("/", ".")
                node = context.static_analysis.resolve_across_languages(qname)
                if node is not None:
                    if node.fully_qualified_name != qname:
                        key_entity.qualified_name = node.fully_qualified_name
                    valid.append(key_entity)
                else:
                    dropped += 1
            component.key_entities = valid

    if dropped:
        logger.info(f"[Validation] Silently dropped {dropped} invalid key entities")

    # Only fail if any component ended up with zero key_entities
    empty_components = [c.name for c in result.components if not c.key_entities]
    if empty_components:
        missing_str = ", ".join(empty_components)
        logger.warning(f"[Validation] Components with no valid key entities after cleanup: {missing_str}")
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
            # Check both directions: the LLM's relation direction may not match
            # the call graph edge direction (e.g. "A uses B" vs B.method() calls A.method())
            if (src_cluster in src_set and dst_cluster in dst_set) or (
                src_cluster in dst_set and dst_cluster in src_set
            ):
                return True

    return False
