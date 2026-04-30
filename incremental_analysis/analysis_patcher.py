import json
import logging
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables.config import RunnableConfig
from trustcall import create_extractor

from agents.agent_responses import AnalysisInsights, LLMBaseModel, Relation, SourceCodeReference

logger = logging.getLogger(__name__)

_PATCH_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class PatchScope:
    scope_id: str | None
    target_component_ids: list[str]
    visited_methods: list[str]
    impacted_methods: list[str]
    synthetic_files: list[str] = field(default_factory=list)
    semantic_impact_summary: str = ""


class ComponentPatch(LLMBaseModel):
    component_id: str
    description: str
    key_entities: list[SourceCodeReference]

    def llm_str(self) -> str:
        return f"{self.component_id}: {self.description}"


class RelationPatch(LLMBaseModel):
    src_id: str
    dst_id: str
    relation: str
    src_name: str | None = None
    dst_name: str | None = None

    def llm_str(self) -> str:
        return f"{self.src_id}->{self.dst_id}: {self.relation}"


class AnalysisScopePatch(LLMBaseModel):
    scope_description: str | None = None
    components: list[ComponentPatch]
    relations: list[RelationPatch]

    def llm_str(self) -> str:
        return json.dumps(
            {
                "scope_description": self.scope_description,
                "components": [component.model_dump() for component in self.components],
                "relations": [relation.model_dump() for relation in self.relations],
            }
        )


def _relation_key_from_ids(src_id: str, dst_id: str) -> str:
    return f"{src_id}->{dst_id}"


def _relation_key(relation: Relation) -> str:
    return _relation_key_from_ids(relation.src_id, relation.dst_id)


def _touches_target_component_ids(src_id: str, dst_id: str, target_component_ids: set[str]) -> bool:
    return src_id in target_component_ids or dst_id in target_component_ids


def _scope_snapshot(analysis: AnalysisInsights, patch_scope: PatchScope) -> dict:
    component_lookup = {component.component_id: component for component in analysis.components}
    targeted_components = []
    for component_id in patch_scope.target_component_ids:
        component = component_lookup.get(component_id)
        if component is None:
            continue
        targeted_components.append(
            {
                "component_id": component.component_id,
                "name": component.name,
                "description": component.description,
                "key_entities": [reference.model_dump(exclude_none=True) for reference in component.key_entities],
                "file_methods": [
                    {
                        "file_path": group.file_path,
                        "methods": [method.qualified_name for method in group.methods],
                    }
                    for group in component.file_methods
                ],
            }
        )

    targeted_set = set(patch_scope.target_component_ids)
    relations = [
        relation.model_dump(exclude_none=True)
        for relation in analysis.components_relations
        if relation.src_id in targeted_set or relation.dst_id in targeted_set
    ]

    return {
        "description": analysis.description,
        "target_component_ids": list(patch_scope.target_component_ids),
        "components": targeted_components,
        "relations": relations,
        "visited_methods": list(patch_scope.visited_methods),
        "impacted_methods": list(patch_scope.impacted_methods),
        "synthetic_files": list(patch_scope.synthetic_files),
        "semantic_impact_summary": patch_scope.semantic_impact_summary,
    }


def _build_patch_prompt(analysis: AnalysisInsights, patch_scope: PatchScope) -> str:
    snapshot = _scope_snapshot(analysis, patch_scope)
    return (
        "Update only the targeted architectural scope.\n"
        "Return replacements only for targeted component descriptions, their key entities, "
        "and relations touching those components.\n"
        "Do not invent new components or change component IDs.\n\n"
        f"```json\n{json.dumps(snapshot, indent=2)}\n```"
    )


def apply_scope_patch(
    analysis: AnalysisInsights,
    patch_scope: PatchScope,
    scope_patch: AnalysisScopePatch,
) -> AnalysisInsights:
    """Apply a structured patch deterministically by stable IDs."""
    patched = analysis.model_copy(deep=True)
    target_component_ids = set(patch_scope.target_component_ids)

    if scope_patch.scope_description:
        patched.description = scope_patch.scope_description

    components_by_id = {component.component_id: component for component in patched.components}
    for component_patch in scope_patch.components:
        if component_patch.component_id not in target_component_ids:
            continue
        component = components_by_id.get(component_patch.component_id)
        if component is None:
            continue
        component.description = component_patch.description
        component.key_entities = [reference.model_copy(deep=True) for reference in component_patch.key_entities]

    returned_relations = {
        _relation_key_from_ids(relation_patch.src_id, relation_patch.dst_id): relation_patch
        for relation_patch in scope_patch.relations
    }
    untouched_relations: list[Relation] = []
    for relation in patched.components_relations:
        touches_target = _touches_target_component_ids(relation.src_id, relation.dst_id, target_component_ids)
        if not touches_target:
            untouched_relations.append(relation)
            continue
        relation_patch = returned_relations.pop(_relation_key(relation), None)
        if relation_patch is None:
            untouched_relations.append(relation)
            continue
        untouched_relations.append(
            Relation(
                relation=relation_patch.relation,
                src_name=relation_patch.src_name or relation.src_name,
                dst_name=relation_patch.dst_name or relation.dst_name,
                src_id=relation_patch.src_id,
                dst_id=relation_patch.dst_id,
            )
        )

    for relation_patch in returned_relations.values():
        if not _touches_target_component_ids(relation_patch.src_id, relation_patch.dst_id, target_component_ids):
            continue
        if relation_patch.src_id not in components_by_id or relation_patch.dst_id not in components_by_id:
            continue
        untouched_relations.append(
            Relation(
                relation=relation_patch.relation,
                src_name=relation_patch.src_name or "",
                dst_name=relation_patch.dst_name or "",
                src_id=relation_patch.src_id,
                dst_id=relation_patch.dst_id,
            )
        )

    patched.components_relations = untouched_relations
    return patched


def patch_analysis_scope(
    analysis: AnalysisInsights,
    patch_scope: PatchScope,
    agent_llm: BaseChatModel,
    callbacks: list | None = None,
) -> AnalysisInsights | None:
    """Patch a bounded analysis scope with a structured extractor contract."""
    extractor = create_extractor(
        agent_llm,
        tools=[AnalysisScopePatch],
        tool_choice=AnalysisScopePatch.__name__,
    )
    invoke_config: RunnableConfig = {"callbacks": callbacks} if callbacks else {}
    prompt = _build_patch_prompt(analysis, patch_scope)

    for attempt in range(1, _PATCH_MAX_ATTEMPTS + 1):
        try:
            result = extractor.invoke(prompt, config=invoke_config)
            responses = result.get("responses", [])
            if not responses:
                logger.warning(
                    "Scope patch extractor returned no responses for %s (attempt %d/%d)",
                    patch_scope.scope_id or "root",
                    attempt,
                    _PATCH_MAX_ATTEMPTS,
                )
                continue

            scope_patch = AnalysisScopePatch.model_validate(responses[0])
            return apply_scope_patch(analysis, patch_scope, scope_patch)
        except Exception as exc:
            logger.warning(
                "Scope patch generation failed for %s (attempt %d/%d): %s",
                patch_scope.scope_id or "root",
                attempt,
                _PATCH_MAX_ATTEMPTS,
                exc,
            )

    return None
