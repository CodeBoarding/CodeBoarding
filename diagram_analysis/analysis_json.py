import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agents.agent_responses import (
    Component,
    Relation,
    AnalysisInsights,
    FileMethodGroup,
    SourceCodeReference,
    assign_component_ids,
)

logger = logging.getLogger(__name__)


class RelationJson(Relation):
    """Relation subclass that includes src_id/dst_id in JSON serialization."""

    src_id: str = Field(default="", description="Component ID of the source.")
    dst_id: str = Field(default="", description="Component ID of the destination.")


class ComponentJson(Component):
    # Override to include in JSON serialization (parent has exclude=True)
    component_id: str = Field(description="Deterministic unique identifier for this component.")
    source_cluster_ids: list[int] = Field(
        description="List of cluster IDs from CFG analysis that this component encompasses.",
        default_factory=list,
    )
    can_expand: bool = Field(
        description="Whether the component can be expanded in detail or not.",
        default=False,
    )
    file_methods: list["FileMethodGroup"] = Field(
        description="All methods/functions belonging to this component, grouped by file.",
        default_factory=list,
    )
    # Exclude intermediate field from JSON output
    source_group_names: list[str] = Field(default_factory=list, exclude=True)
    # Nested sub-analysis for expanded components
    components: list["ComponentJson"] | None = Field(
        description="Sub-components if expanded, None otherwise.", default=None
    )
    components_relations: list[RelationJson] | None = Field(
        description="Relations among sub-components if expanded, None otherwise.",
        default=None,
    )


class NotAnalyzedFile(BaseModel):
    path: str = Field(description="Relative path of the file.")
    reason: str = Field(description="Exclusion reason for the file.")


class FileCoverageSummary(BaseModel):
    total_files: int = Field(description="Total number of text files in the repository.")
    analyzed: int = Field(description="Number of files included in the analysis.")
    not_analyzed: int = Field(description="Number of files excluded from the analysis.")
    not_analyzed_by_reason: dict[str, int] = Field(
        default_factory=dict, description="Count of excluded files grouped by reason."
    )


class FileCoverageReport(BaseModel):
    version: int = Field(default=1, description="Schema version of the file coverage report.")
    generated_at: str = Field(description="ISO timestamp of when the report was generated.")
    analyzed_files: list[str] = Field(description="List of analyzed file paths.")
    not_analyzed_files: list[NotAnalyzedFile] = Field(description="List of excluded files with optional reasons.")
    summary: FileCoverageSummary = Field(description="Aggregated coverage counts.")


class AnalysisMetadata(BaseModel):
    generated_at: str = Field(description="ISO timestamp of when the analysis was generated.")
    repo_name: str = Field(description="Name of the analyzed repository.")
    depth_level: int = Field(description="Maximum depth level of the analysis.")
    file_coverage_summary: FileCoverageSummary = Field(
        default_factory=lambda: FileCoverageSummary(
            total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={}
        ),
        description="Lightweight file coverage counts.",
    )


class UnifiedAnalysisJson(BaseModel):
    metadata: AnalysisMetadata = Field(description="Metadata about the analysis run.")
    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph."
    )
    components: list[ComponentJson] = Field(description="List of the components identified in the project.")
    components_relations: list[RelationJson] = Field(description="List of relations among the components.")


def from_component_to_json_component(
    component: Component,
    expandable_components: list[Component],
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
    processed_ids: set[str] | None = None,
) -> ComponentJson:
    """Convert a Component to a ComponentJson, optionally nesting sub-analysis data."""
    if processed_ids is None:
        processed_ids = set()

    component_id_val: str = component.component_id
    if component_id_val in processed_ids:
        logger.warning(f"Component {component.name} (ID: {component_id_val}) already processed, skipping expansion")
        can_expand = False
    else:
        processed_ids.add(component_id_val)
        can_expand = any(c.component_id == component.component_id for c in expandable_components)

    nested_components: list[ComponentJson] | None = None
    nested_relations: list[RelationJson] | None = None

    if can_expand and sub_analyses and component.component_id in sub_analyses:
        sub_analysis, sub_expandable = sub_analyses[component.component_id]
        nested_components = [
            from_component_to_json_component(c, sub_expandable, sub_analyses, processed_ids)
            for c in sub_analysis.components
        ]
        nested_relations = [
            RelationJson(
                relation=r.relation,
                src_name=r.src_name,
                dst_name=r.dst_name,
                src_id=r.src_id,
                dst_id=r.dst_id,
            )
            for r in sub_analysis.components_relations
        ]

    return ComponentJson(
        name=component.name,
        component_id=component.component_id,
        description=component.description,
        key_entities=component.key_entities,
        source_cluster_ids=component.source_cluster_ids,
        file_methods=component.file_methods,
        can_expand=can_expand,
        components=nested_components,
        components_relations=nested_relations,
    )


def from_analysis_to_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
) -> str:
    """Convert an AnalysisInsights to a flat JSON string (legacy-compatible, no metadata wrapper)."""
    components_json = [
        from_component_to_json_component(c, expandable_components, sub_analyses, None) for c in analysis.components
    ]
    # Build a dict matching the old AnalysisInsightsJson shape but with nested components
    relations_json = [
        RelationJson(
            relation=r.relation,
            src_name=r.src_name,
            dst_name=r.dst_name,
            src_id=r.src_id,
            dst_id=r.dst_id,
        )
        for r in analysis.components_relations
    ]
    data = {
        "description": analysis.description,
        "components": [c.model_dump(exclude_none=True) for c in components_json],
        "components_relations": [r.model_dump() for r in relations_json],
    }
    import json

    return json.dumps(data, indent=2)


def _compute_depth_level(
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None,
) -> int:
    """Compute the maximum depth level from the sub_analyses structure.

    Returns 1 if there are no sub-analyses (root only), 2 if there is one level of
    sub-analyses, etc. Recursively traverses nested sub-analyses to find true max depth.
    """
    if not sub_analyses:
        return 1

    def get_depth(analysis: AnalysisInsights, visited: set[str]) -> int:
        """Recursively compute depth for a sub-analysis."""
        max_depth = 1
        for comp in analysis.components:
            if comp.component_id in sub_analyses and comp.component_id not in visited:
                visited.add(comp.component_id)
                sub_analysis, _ = sub_analyses[comp.component_id]
                child_depth = 1 + get_depth(sub_analysis, visited)
                max_depth = max(max_depth, child_depth)
                visited.remove(comp.component_id)
        return max_depth

    max_depth = 1
    for cid, (sub_analysis, _) in sub_analyses.items():
        # Only compute depth for root-level sub-analyses (not referenced by others)
        is_root_level = True
        for other_cid, (other_analysis, _) in sub_analyses.items():
            if other_cid != cid:
                for comp in other_analysis.components:
                    if comp.component_id == cid:
                        is_root_level = False
                        break
            if not is_root_level:
                break

        if is_root_level:
            visited = {cid}
            depth = 1 + get_depth(sub_analysis, visited)
            max_depth = max(max_depth, depth)

    return max_depth


def build_unified_analysis_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    repo_name: str,
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
    file_coverage_summary: FileCoverageSummary | None = None,
) -> str:
    """Build the full unified analysis JSON with metadata and nested sub-analyses.

    The depth_level metadata is computed automatically from the sub_analyses structure
    if not provided explicitly.
    """
    components_json = [
        from_component_to_json_component(c, expandable_components, sub_analyses, None) for c in analysis.components
    ]

    # Use default summary if none provided
    if file_coverage_summary is None:
        summary = FileCoverageSummary(total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={})
    else:
        summary = file_coverage_summary

    relations_json = [
        RelationJson(
            relation=r.relation,
            src_name=r.src_name,
            dst_name=r.dst_name,
            src_id=r.src_id,
            dst_id=r.dst_id,
        )
        for r in analysis.components_relations
    ]
    unified = UnifiedAnalysisJson(
        metadata=AnalysisMetadata(
            generated_at=datetime.now(timezone.utc).isoformat(),
            repo_name=repo_name,
            depth_level=_compute_depth_level(sub_analyses),
            file_coverage_summary=summary,
        ),
        description=analysis.description,
        components=components_json,
        components_relations=relations_json,
    )
    return unified.model_dump_json(indent=2, exclude_none=True)


def parse_unified_analysis(
    data: dict,
) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
    """Parse a unified analysis JSON dict into root AnalysisInsights and sub-analyses.

    Returns:
        (root_analysis, sub_analyses_dict) where sub_analyses_dict maps component_id
        to its nested AnalysisInsights.
    """
    sub_analyses: dict[str, AnalysisInsights] = {}
    root_analysis = _extract_analysis_recursive(data, sub_analyses)

    # Backward compatibility: if components lack component_id, assign deterministically
    if any(c.component_id == "" for c in root_analysis.components):
        _assign_ids_and_rekey(root_analysis, sub_analyses)

    return root_analysis, sub_analyses


def _assign_ids_and_rekey(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> None:
    """Assign component IDs to an analysis loaded from old JSON (without IDs) and re-key sub_analyses."""
    from agents.agent_responses import ROOT_PARENT_ID

    # Build old name -> sub_analysis mapping before clearing
    old_subs = dict(sub_analyses)
    sub_analyses.clear()

    # Assign IDs to root and recursively to sub-analyses
    _assign_ids_recursive(root_analysis, old_subs, sub_analyses, ROOT_PARENT_ID)


def _assign_ids_recursive(
    analysis: AnalysisInsights,
    old_subs: dict[str, AnalysisInsights],
    new_subs: dict[str, AnalysisInsights],
    parent_id: str,
) -> None:
    """Recursively assign IDs and re-key sub_analyses from name-keyed to id-keyed."""
    assign_component_ids(analysis, parent_id=parent_id)
    for comp in analysis.components:
        # Check if this component had a sub-analysis keyed by name
        if comp.name in old_subs:
            sub = old_subs[comp.name]
            new_subs[comp.component_id] = sub
            _assign_ids_recursive(sub, old_subs, new_subs, comp.component_id)


def build_id_to_name_map(root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> dict[str, str]:
    """Build a mapping from component_id to component name across all analysis levels."""
    id_to_name: dict[str, str] = {c.component_id: c.name for c in root_analysis.components}
    for sub_analysis in sub_analyses.values():
        for comp in sub_analysis.components:
            id_to_name[comp.component_id] = comp.name
    return id_to_name


def _extract_analysis_recursive(data: dict, sub_analyses: dict[str, AnalysisInsights]) -> AnalysisInsights:
    """Recursively extract AnalysisInsights from data dict, collecting all sub-analyses.

    Args:
        data: The analysis data dict containing components, description, etc.
        sub_analyses: Dict to populate with component_id -> AnalysisInsights mappings.

    Returns:
        AnalysisInsights for this level (components are non-nested at this level).
    """
    components: list[Component] = []

    for comp_data in data.get("components", []):
        file_methods = [FileMethodGroup(**fm) for fm in comp_data.get("file_methods", [])]
        key_entities = [
            SourceCodeReference(
                qualified_name=ke["qualified_name"],
                reference_file=ke.get("reference_file"),
                reference_start_line=ke.get("reference_start_line", 0),
                reference_end_line=ke.get("reference_end_line", 0),
            )
            for ke in comp_data.get("key_entities", [])
        ]

        # Create the component for this level (non-nested)
        component = Component(
            name=comp_data["name"],
            component_id=comp_data.get("component_id", ""),
            description=comp_data["description"],
            key_entities=key_entities,
            file_methods=file_methods,
            source_cluster_ids=comp_data.get("source_cluster_ids", []),
        )
        components.append(component)

        # Recursively process nested components if they exist
        nested_components = comp_data.get("components")
        if nested_components is not None:
            sub_analysis = _extract_analysis_recursive(comp_data, sub_analyses)
            sub_analyses[component.component_id or comp_data["name"]] = sub_analysis

    return AnalysisInsights(
        description=data.get("description", ""),
        components=components,
        components_relations=[Relation(**r) for r in data.get("components_relations", [])],
    )
