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


class AnalysisMetadata(BaseModel):
    generated_at: str = Field(description="ISO timestamp of when the analysis was generated.")
    repo_name: str = Field(description="Name of the analyzed repository.")
    depth_level: int = Field(description="Maximum depth level of the analysis.")


class UnifiedAnalysisJson(BaseModel):
    version: int = Field(default=2, description="Schema version of the unified analysis format.")
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


def build_unified_analysis_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    repo_name: str,
    depth_level: int,
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
) -> str:
    """Build the full unified analysis JSON with metadata and nested sub-analyses."""
    components_json = [
        from_component_to_json_component(c, expandable_components, sub_analyses, None) for c in analysis.components
    ]

    unified = UnifiedAnalysisJson(
        version=2,
        metadata=AnalysisMetadata(
            generated_at=datetime.now(timezone.utc).isoformat(),
            repo_name=repo_name,
            depth_level=depth_level,
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
    # Extract root-level analysis
    root_components: list[Component] = []
    sub_analyses: dict[str, AnalysisInsights] = {}

    for comp_data in data.get("components", []):
        # Build the root Component (without nested children)
        component = Component(
            name=comp_data["name"],
            description=comp_data["description"],
            key_entities=comp_data.get("key_entities", []),
            assigned_files=comp_data.get("assigned_files", []),
            source_cluster_ids=comp_data.get("source_cluster_ids", []),
        )
        root_components.append(component)

        # If this component has nested sub-analysis, extract it
        if comp_data.get("components") is not None:
            sub_components: list[Component] = []
            nested_sub_analyses: dict[str, AnalysisInsights] = {}

            for sub_comp_data in comp_data["components"]:
                sub_component = Component(
                    name=sub_comp_data["name"],
                    description=sub_comp_data["description"],
                    key_entities=sub_comp_data.get("key_entities", []),
                    assigned_files=sub_comp_data.get("assigned_files", []),
                    source_cluster_ids=sub_comp_data.get("source_cluster_ids", []),
                )
                sub_components.append(sub_component)

                # Recursively handle deeper nesting
                if sub_comp_data.get("components") is not None:
                    deeper_sub = _extract_sub_analysis(sub_comp_data)
                    nested_sub_analyses[sub_comp_data["name"]] = deeper_sub

            sub_analysis = AnalysisInsights(
                description=comp_data.get("description", ""),
                components=sub_components,
                components_relations=[Relation(**r) for r in comp_data.get("components_relations", [])],
            )
            # Assign assigned_files to sub_components
            for sub_comp, sub_comp_data in zip(sub_components, comp_data["components"]):
                sub_comp.assigned_files = sub_comp_data.get("assigned_files", [])

            sub_analyses[comp_data["name"]] = sub_analysis

            # Recurse deeper
            for name, deeper in nested_sub_analyses.items():
                sub_analyses[name] = deeper

    root_analysis = AnalysisInsights(
        description=data.get("description", ""),
        components=root_components,
        components_relations=[Relation(**r) for r in data.get("components_relations", [])],
    )

    # Assign assigned_files to root components
    for comp, comp_data in zip(root_components, data.get("components", [])):
        comp.assigned_files = comp_data.get("assigned_files", [])

    return root_analysis, sub_analyses


def _extract_sub_analysis(comp_data: dict) -> AnalysisInsights:
    """Extract a sub-analysis from a nested component data dict."""
    sub_components: list[Component] = []
    for sub_comp_data in comp_data.get("components", []):
        sub_component = Component(
            name=sub_comp_data["name"],
            description=sub_comp_data["description"],
            key_entities=sub_comp_data.get("key_entities", []),
            assigned_files=sub_comp_data.get("assigned_files", []),
            source_cluster_ids=sub_comp_data.get("source_cluster_ids", []),
        )
        sub_components.append(sub_component)

    return AnalysisInsights(
        description=comp_data.get("description", ""),
        components=sub_components,
        components_relations=[Relation(**r) for r in comp_data.get("components_relations", [])],
    )
