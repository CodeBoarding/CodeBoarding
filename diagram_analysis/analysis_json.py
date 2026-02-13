import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agents.agent_responses import Component, Relation, AnalysisInsights

logger = logging.getLogger(__name__)


class ComponentJson(Component):
    can_expand: bool = Field(
        description="Whether the component can be expanded in detail or not.",
        default=False,
    )
    assigned_files: list[str] = Field(
        description="A list of source code names of files assigned to the component.",
        default_factory=list,
    )
    # Nested sub-analysis for expanded components
    components: list["ComponentJson"] | None = Field(
        description="Sub-components if expanded, None otherwise.", default=None
    )
    components_relations: list[Relation] | None = Field(
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
    components_relations: list[Relation] = Field(description="List of relations among the components.")


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
        can_expand = any(c.name == component.name for c in expandable_components)

    nested_components: list[ComponentJson] | None = None
    nested_relations: list[Relation] | None = None

    if can_expand and sub_analyses and component.name in sub_analyses:
        sub_analysis, sub_expandable = sub_analyses[component.name]
        nested_components = [
            from_component_to_json_component(c, sub_expandable, sub_analyses, processed_ids)
            for c in sub_analysis.components
        ]
        nested_relations = sub_analysis.components_relations

    return ComponentJson(
        name=component.name,
        description=component.description,
        key_entities=component.key_entities,
        source_cluster_ids=component.source_cluster_ids,
        assigned_files=component.assigned_files,
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
    data = {
        "description": analysis.description,
        "components": [c.model_dump(exclude_none=True) for c in components_json],
        "components_relations": [r.model_dump() for r in analysis.components_relations],
    }
    import json

    return json.dumps(data, indent=2)


def _compute_depth_level(
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None,
) -> int:
    """Compute the maximum depth level from the sub_analyses structure.

    Returns 1 if there are no sub-analyses (root only), 2 if there is one level of
    sub-analyses, etc.
    """
    if not sub_analyses:
        return 1

    max_child_depth = 1
    for _, (sub_analysis, _) in sub_analyses.items():
        # Check if any of this sub-analysis's components themselves have sub-analyses
        child_depth = 1
        for comp in sub_analysis.components:
            if comp.name in sub_analyses:
                child_depth = 2
                break
        max_child_depth = max(max_child_depth, child_depth)

    return 1 + max_child_depth


def build_unified_analysis_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    repo_name: str,
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
    file_coverage_summary: FileCoverageSummary | None = None,
) -> str:
    """Build the full unified analysis JSON with metadata and nested sub-analyses.

    The depth_level metadata is computed automatically from the sub_analyses structure.
    """
    components_json = [
        from_component_to_json_component(c, expandable_components, sub_analyses, None) for c in analysis.components
    ]

    # Use default summary if none provided
    if file_coverage_summary is None:
        summary = FileCoverageSummary(total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={})
    else:
        summary = file_coverage_summary

    unified = UnifiedAnalysisJson(
        metadata=AnalysisMetadata(
            generated_at=datetime.now(timezone.utc).isoformat(),
            repo_name=repo_name,
            depth_level=_compute_depth_level(sub_analyses),
            file_coverage_summary=summary,
        ),
        description=analysis.description,
        components=components_json,
        components_relations=analysis.components_relations,
    )
    return unified.model_dump_json(indent=2, exclude_none=True)


def parse_unified_analysis(
    data: dict,
) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
    """Parse a unified analysis JSON dict into root AnalysisInsights and sub-analyses.

    Returns:
        (root_analysis, sub_analyses_dict) where sub_analyses_dict maps component name
        to its nested AnalysisInsights.
    """
    sub_analyses: dict[str, AnalysisInsights] = {}
    root_analysis = _extract_analysis_recursive(data, sub_analyses)
    return root_analysis, sub_analyses


def _extract_analysis_recursive(data: dict, sub_analyses: dict[str, AnalysisInsights]) -> AnalysisInsights:
    """Recursively extract AnalysisInsights from data dict, collecting all sub-analyses.

    Args:
        data: The analysis data dict containing components, description, etc.
        sub_analyses: Dict to populate with component name -> AnalysisInsights mappings.

    Returns:
        AnalysisInsights for this level (components are non-nested at this level).
    """
    components: list[Component] = []

    for comp_data in data.get("components", []):
        # Create the component for this level (non-nested)
        component = Component(
            name=comp_data["name"],
            description=comp_data["description"],
            key_entities=comp_data.get("key_entities", []),
            assigned_files=comp_data.get("assigned_files", []),
            source_cluster_ids=comp_data.get("source_cluster_ids", []),
        )
        components.append(component)

        # Recursively process nested components if they exist
        nested_components = comp_data.get("components")
        if nested_components is not None:
            sub_analysis = _extract_analysis_recursive(comp_data, sub_analyses)
            sub_analyses[comp_data["name"]] = sub_analysis

    return AnalysisInsights(
        description=data.get("description", ""),
        components=components,
        components_relations=[Relation(**r) for r in data.get("components_relations", [])],
    )
