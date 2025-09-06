from typing import List

from pydantic import BaseModel, Field

from agents.agent_responses import Component, Relation, AnalysisInsights


class ComponentJson(Component):
    can_expand: bool = Field(
        description="Whether the component can be expanded in detail or not.",
        default=False
    )
    assigned_files: List[str] = Field(
        description="A list of source code names of files assigned to the component.",
        default_factory=list
    )


class AnalysisInsightsJson(BaseModel):
    description: str = Field(
        "One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose.")
    components: List[ComponentJson] = Field(
        description="List of the components identified in the project.")
    components_relations: List[Relation] = Field(
        description="List of relations among the components."
    )


def from_analysis_to_json(analysis: AnalysisInsights, new_components: List[Component]) -> str:
    components_json = [from_component_to_json_component(c, new_components) for c in analysis.components]
    analysis_json = AnalysisInsightsJson(
        description=analysis.description,
        components=components_json,
        components_relations=analysis.components_relations
    )
    return analysis_json.model_dump_json(indent=2)


def from_component_to_json_component(component: Component, new_components: List[Component]) -> ComponentJson:
    can_expand = any(c.name == component.name for c in new_components)
    return ComponentJson(
        name=component.name,
        description=component.description,
        referenced_source_code=component.referenced_source_code,
        assigned_files=component.assigned_files,
        can_expand=can_expand
    )
