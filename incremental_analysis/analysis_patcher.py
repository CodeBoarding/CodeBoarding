import json
import logging
import os
from dataclasses import dataclass, field

_PATCHER_DEBUG = os.environ.get("CB_PATCHER_DEBUG") == "1"

from langchain_core.messages import HumanMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables.config import RunnableConfig
from pydantic import ValidationError
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


def _relation_name_key(src_name: str, dst_name: str) -> tuple[str, str]:
    return src_name, dst_name


def _count_target_relation_names(
    relations: list[Relation], target_component_ids: set[str]
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for relation in relations:
        if not _touches_target_component_ids(relation.src_id, relation.dst_id, target_component_ids):
            continue
        name_key = _relation_name_key(relation.src_name, relation.dst_name)
        counts[name_key] = counts.get(name_key, 0) + 1
    return counts


def _pop_relation_patch_by_names(
    returned_relations: dict[str, "RelationPatch"],
    relation: Relation,
    existing_name_counts: dict[tuple[str, str], int],
) -> "RelationPatch | None":
    name_key = _relation_name_key(relation.src_name, relation.dst_name)
    if existing_name_counts.get(name_key, 0) != 1:
        if _PATCHER_DEBUG:
            logger.info(
                "[CB_PATCHER_DEBUG] apply: cannot fall back by names for %s->%s because existing relation names are ambiguous",
                relation.src_id,
                relation.dst_id,
            )
        return None

    matching_keys = [
        key
        for key, relation_patch in returned_relations.items()
        if relation_patch.src_name == relation.src_name and relation_patch.dst_name == relation.dst_name
    ]
    if len(matching_keys) != 1:
        if _PATCHER_DEBUG:
            logger.info(
                "[CB_PATCHER_DEBUG] apply: cannot fall back by names for %s->%s because returned relation names matched %d patches",
                relation.src_id,
                relation.dst_id,
                len(matching_keys),
            )
        return None

    relation_patch = returned_relations.pop(matching_keys[0])
    if _PATCHER_DEBUG:
        logger.info(
            "[CB_PATCHER_DEBUG] apply: matched %s->%s by names %r -> %r",
            relation.src_id,
            relation.dst_id,
            relation.src_name,
            relation.dst_name,
        )
    return relation_patch


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
    relations = []
    for relation in analysis.components_relations:
        if relation.src_id not in targeted_set and relation.dst_id not in targeted_set:
            continue
        # Relation IDs are excluded from generic LLM serialization, but the
        # patcher must carry them explicitly so relation replacements can be
        # applied deterministically even when only one endpoint is in scope.
        payload = relation.model_dump(exclude_none=True)
        payload["src_id"] = relation.src_id
        payload["dst_id"] = relation.dst_id
        relations.append(payload)

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
    prompt = (
        "Update only the targeted architectural scope.\n"
        "Return replacements only for targeted component descriptions, their key entities, "
        "and relations touching those components.\n"
        "Do not invent new components or change component IDs.\n"
        "If `semantic_impact_summary` is non-empty and contradicts an existing relation "
        "label or component description in the targeted scope, you MUST issue a "
        "replacement that reflects the new semantics. Do not retain a label that the "
        "summary contradicts.\n\n"
        f"```json\n{json.dumps(snapshot, indent=2)}\n```"
    )
    if _PATCHER_DEBUG:
        logger.info(
            "[CB_PATCHER_DEBUG] _build_patch_prompt scope_id=%s target_component_ids=%s "
            "components_in_payload=%s relations_in_payload=%d "
            "semantic_impact_summary=%r visited=%d impacted=%d",
            patch_scope.scope_id,
            list(patch_scope.target_component_ids),
            [c["component_id"] for c in snapshot.get("components", [])],
            len(snapshot.get("relations", [])),
            patch_scope.semantic_impact_summary,
            len(patch_scope.visited_methods),
            len(patch_scope.impacted_methods),
        )
        for rel in snapshot.get("relations", []):
            logger.info(
                "[CB_PATCHER_DEBUG] payload_relation %s->%s: %s",
                rel.get("src_id"),
                rel.get("dst_id"),
                rel.get("relation"),
            )
    return prompt


def _response_to_payload(response: object) -> dict:
    if isinstance(response, AnalysisScopePatch):
        return response.model_dump(exclude_none=True)
    if hasattr(response, "model_dump"):
        return response.model_dump(exclude_none=True)
    if isinstance(response, dict):
        return response
    raise TypeError(f"Unexpected patch response type: {type(response)!r}")


def _salvage_stringified_key_entities(payload: dict) -> dict:
    fixed = json.loads(json.dumps(payload))
    for component in fixed.get("components", []):
        key_entities = component.get("key_entities")
        if not isinstance(key_entities, list):
            continue
        repaired = []
        for reference in key_entities:
            if isinstance(reference, str):
                try:
                    parsed = json.loads(reference)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    reference = parsed
            repaired.append(reference)
        component["key_entities"] = repaired
    return fixed


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors(include_url=False):
        location = ".".join(str(part) for part in err["loc"])
        lines.append(f"- {location}: {err['msg']}")
    return "\n".join(lines)


def _build_empty_response_feedback() -> HumanMessage:
    return HumanMessage(
        content=(
            "Your previous response did not produce a valid AnalysisScopePatch tool output.\n"
            "Return the full AnalysisScopePatch and call the tool exactly once."
        )
    )


def _build_validation_feedback(raw_response: dict, exc: ValidationError) -> HumanMessage:
    return HumanMessage(
        content=(
            "Your previous AnalysisScopePatch output failed validation.\n"
            "Return the full AnalysisScopePatch again.\n"
            "Rules:\n"
            "- Do not change component IDs.\n"
            "- Do not change relation src_id/dst_id.\n"
            "- Every components[].key_entities[] item must be an object, never a JSON string.\n\n"
            f"Validation errors:\n{_format_validation_error(exc)}\n\n"
            "Previous invalid output:\n"
            f"```json\n{json.dumps(raw_response, indent=2)}\n```"
        )
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
    existing_relation_name_counts = _count_target_relation_names(patched.components_relations, target_component_ids)
    untouched_relations: list[Relation] = []
    for relation in patched.components_relations:
        touches_target = _touches_target_component_ids(relation.src_id, relation.dst_id, target_component_ids)
        if not touches_target:
            untouched_relations.append(relation)
            if _PATCHER_DEBUG:
                logger.info(
                    "[CB_PATCHER_DEBUG] apply: kept %s->%s (does not touch targeted_set=%s)",
                    relation.src_id,
                    relation.dst_id,
                    sorted(target_component_ids),
                )
            continue
        relation_patch = returned_relations.pop(_relation_key(relation), None)
        if relation_patch is None:
            relation_patch = _pop_relation_patch_by_names(returned_relations, relation, existing_relation_name_counts)
        if relation_patch is None:
            untouched_relations.append(relation)
            if _PATCHER_DEBUG:
                logger.info(
                    "[CB_PATCHER_DEBUG] apply: kept %s->%s (touches targeted_set=%s but LLM returned no matching patch by ID or unique names; existing label=%r)",
                    relation.src_id,
                    relation.dst_id,
                    sorted(target_component_ids),
                    relation.relation[:150],
                )
            continue
        if _PATCHER_DEBUG:
            logger.info(
                "[CB_PATCHER_DEBUG] apply: REPLACED %s->%s; old=%r new=%r",
                relation.src_id,
                relation.dst_id,
                relation.relation[:150],
                relation_patch.relation[:150],
            )
        untouched_relations.append(
            Relation(
                relation=relation_patch.relation,
                src_name=relation_patch.src_name or relation.src_name,
                dst_name=relation_patch.dst_name or relation.dst_name,
                src_id=relation.src_id,
                dst_id=relation.dst_id,
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
    messages = [HumanMessage(content=_build_patch_prompt(analysis, patch_scope))]

    for attempt in range(1, _PATCH_MAX_ATTEMPTS + 1):
        raw_response: dict | None = None
        try:
            result = extractor.invoke({"messages": messages}, config=invoke_config)
            responses = result.get("responses", [])
            if not responses:
                logger.warning(
                    "Scope patch extractor returned no responses for %s (attempt %d/%d)",
                    patch_scope.scope_id or "root",
                    attempt,
                    _PATCH_MAX_ATTEMPTS,
                )
                messages.append(_build_empty_response_feedback())
                continue

            raw_response = _response_to_payload(responses[0])
            scope_patch = AnalysisScopePatch.model_validate(_salvage_stringified_key_entities(raw_response))
            if _PATCHER_DEBUG:
                logger.info(
                    "[CB_PATCHER_DEBUG] llm_response scope_id=%s components_returned=%d relations_returned=%d",
                    patch_scope.scope_id,
                    len(scope_patch.components),
                    len(scope_patch.relations),
                )
                for cp in scope_patch.components:
                    logger.info(
                        "[CB_PATCHER_DEBUG] llm_returned_component cid=%s desc=%r",
                        cp.component_id,
                        cp.description[:200],
                    )
                for rp in scope_patch.relations:
                    logger.info(
                        "[CB_PATCHER_DEBUG] llm_returned_relation %s->%s: %r",
                        rp.src_id,
                        rp.dst_id,
                        rp.relation[:300],
                    )
            return apply_scope_patch(analysis, patch_scope, scope_patch)
        except ValidationError as exc:
            logger.warning(
                "Scope patch generation failed for %s (attempt %d/%d): %s",
                patch_scope.scope_id or "root",
                attempt,
                _PATCH_MAX_ATTEMPTS,
                exc,
            )
            if raw_response is not None:
                messages.append(_build_validation_feedback(raw_response, exc))
        except Exception as exc:
            logger.warning(
                "Scope patch generation failed for %s (attempt %d/%d): %s",
                patch_scope.scope_id or "root",
                attempt,
                _PATCH_MAX_ATTEMPTS,
                exc,
            )

    return None
