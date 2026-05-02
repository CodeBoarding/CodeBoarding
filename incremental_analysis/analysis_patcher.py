import json
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables.config import RunnableConfig
from pydantic import ValidationError
from trustcall import create_extractor

from pydantic import Field

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    LLMBaseModel,
    Relation,
    SourceCodeReference,
)

logger = logging.getLogger(__name__)

_PATCH_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class PatchScope:
    scope_id: str | None
    target_component_ids: list[str]
    visited_methods: list[str]
    impacted_methods: list[str]
    synthetic_files: list[str] = field(default_factory=list)
    unallocated_files: list[str] = field(default_factory=list)
    semantic_impact_summary: str = ""


class ComponentPatch(LLMBaseModel):
    component_id: str
    description: str
    key_entities: list[SourceCodeReference]
    added_files: list[str] = Field(
        default_factory=list,
        description=(
            "File paths from `unallocated_files` that should be folded into this "
            "existing component's file_methods. Server materializes file_methods "
            "from the analysis files index."
        ),
    )

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


class NewComponentSpec(LLMBaseModel):
    """Proposal for a brand-new component the patcher should mint.

    Mirrors the LLM-visible subset of `Component`: the model proposes the
    intent (name, description, key_entities); the server assigns the
    `component_id` deterministically from the scope's namespace. Following
    the same convention `Component.component_id` already uses (``exclude=True``).
    """

    name: str = Field(description="Name of the new component.")
    description: str = Field(description="A short description of the new component.")
    key_entities: list[SourceCodeReference] = Field(
        description="2-5 key entities (classes/methods) representing the new component's core functionality."
    )
    owned_files: list[str] = Field(
        default_factory=list,
        description=(
            "File paths from `unallocated_files` that this new component owns. "
            "Server materializes file_methods from the analysis files index."
        ),
    )

    def llm_str(self) -> str:
        return f"NEW: {self.name}: {self.description}"


class AnalysisScopePatch(LLMBaseModel):
    scope_description: str | None = None
    components: list[ComponentPatch]
    new_components: list[NewComponentSpec] = Field(default_factory=list)
    relations: list[RelationPatch]

    def llm_str(self) -> str:
        return json.dumps(
            {
                "scope_description": self.scope_description,
                "components": [component.model_dump() for component in self.components],
                "new_components": [component.model_dump() for component in self.new_components],
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
        return None

    matching_keys = [
        key
        for key, relation_patch in returned_relations.items()
        if relation_patch.src_name == relation.src_name and relation_patch.dst_name == relation.dst_name
    ]
    if len(matching_keys) != 1:
        return None

    return returned_relations.pop(matching_keys[0])


def _assign_new_component_id(scope_id: str | None, existing_ids: set[str]) -> str:
    """Mint the next dotted-suffix component id within the scope's namespace.

    Examples:
        scope_id=None  → ``"6"`` if ``{"1","2",…,"5"}`` exist
        scope_id="1"   → ``"1.6"`` if ``{"1.1",…,"1.5"}`` exist
        scope_id="1.1" → ``"1.1.4"`` if ``{"1.1.1","1.1.2","1.1.3"}`` exist

    Pure: deterministic given (scope_id, existing_ids). The server owns this
    namespace; the LLM never sees ``component_id`` and never picks one — same
    convention `Component.component_id` already enforces via ``exclude=True``.
    """
    prefix = "" if scope_id is None else f"{scope_id}."
    used_suffixes: set[int] = set()
    for cid in existing_ids:
        if not cid.startswith(prefix):
            continue
        rest = cid[len(prefix) :]
        if "." in rest:
            continue
        try:
            used_suffixes.add(int(rest))
        except ValueError:
            continue
    candidate = 1
    while candidate in used_suffixes:
        candidate += 1
    return f"{prefix}{candidate}"


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
        "unallocated_files": list(patch_scope.unallocated_files),
        "semantic_impact_summary": patch_scope.semantic_impact_summary,
    }


def _build_patch_prompt(analysis: AnalysisInsights, patch_scope: PatchScope) -> str:
    snapshot = _scope_snapshot(analysis, patch_scope)
    return (
        "Update only the targeted architectural scope.\n"
        "Patch existing component descriptions, their key entities, "
        "and relations touching those components via `components` and `relations`.\n"
        "Do not change existing component IDs or names.\n\n"
        "Every path in `unallocated_files` MUST be allocated by this response — "
        "either by listing it in an existing component's `added_files` (fold-in), "
        "or in a new component's `owned_files` (creation). Do not leave files "
        "unallocated.\n"
        "Mint a new entry in `new_components` (with a `name` not already used in "
        "the snapshot) when the unallocated files form a cohesive new subsystem; "
        "use `added_files` on a single existing component when they are a natural "
        "extension of that component's responsibility.\n"
        "Each path in `unallocated_files` may appear in at most one of "
        "`components[*].added_files` or `new_components[*].owned_files`.\n\n"
        f"```json\n{json.dumps(snapshot, indent=2)}\n```"
    )


def _response_to_payload(response: object) -> dict:
    if isinstance(response, AnalysisScopePatch):
        return response.model_dump(exclude_none=True)
    if hasattr(response, "model_dump"):
        return response.model_dump(exclude_none=True)
    if isinstance(response, dict):
        return response
    raise TypeError(f"Unexpected patch response type: {type(response)!r}")


def _salvage_stringified_objects(payload: Any) -> Any:
    """Recursively coerce JSON-encoded strings back into structured data."""
    if isinstance(payload, str):
        stripped = payload.strip()
        if stripped.startswith(("{", "[")) and stripped.endswith(("}", "]")):
            try:
                return _salvage_stringified_objects(json.loads(stripped))
            except json.JSONDecodeError:
                return payload
        return payload
    if isinstance(payload, list):
        return [_salvage_stringified_objects(item) for item in payload]
    if isinstance(payload, dict):
        return {key: _salvage_stringified_objects(value) for key, value in payload.items()}
    return payload


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
            "- Do not change existing component IDs or names.\n"
            "- Use `new_components` (no component_id) for additions, `components` (with component_id) for patches.\n"
            "- Allocate each path in `unallocated_files` exactly once, via `added_files` on an existing component or `owned_files` on a new one.\n"
            "- Do not change relation src_id/dst_id for existing components.\n"
            "- Every nested object in the payload must be an object, never a JSON-encoded string.\n\n"
            f"Validation errors:\n{_format_validation_error(exc)}\n\n"
            "Previous invalid output:\n"
            f"```json\n{json.dumps(raw_response, indent=2)}\n```"
        )
    )


def _file_methods_for_paths(
    analysis: AnalysisInsights,
    file_paths: list[str],
    already_allocated: set[str],
) -> list[FileMethodGroup]:
    """Materialize ``FileMethodGroup`` entries for *file_paths* from the analysis files index.

    Skips paths not in the index, paths already allocated within this patch
    (so the LLM can't double-attribute one file to two components), and paths
    already present (caller appends to an existing list).
    """
    groups: list[FileMethodGroup] = []
    for file_path in file_paths:
        if file_path in already_allocated:
            continue
        entry = analysis.files.get(file_path)
        if entry is None:
            logger.warning("Skipping file_methods for %s: not present in analysis.files", file_path)
            continue
        groups.append(
            FileMethodGroup(
                file_path=file_path,
                methods=[m.model_copy(deep=True) for m in entry.methods],
            )
        )
        already_allocated.add(file_path)
    return groups


def apply_scope_patch(
    analysis: AnalysisInsights,
    patch_scope: PatchScope,
    scope_patch: AnalysisScopePatch,
) -> AnalysisInsights:
    """Apply a structured patch deterministically by stable IDs."""
    patched = analysis.model_copy(deep=True)
    target_component_ids = set(patch_scope.target_component_ids)
    unallocated_set = set(patch_scope.unallocated_files)
    # Tracks file paths already taken by this patch — guards against the LLM
    # double-attributing the same file to several components.
    allocated_within_patch: set[str] = set()

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

        # Fold any LLM-allocated unallocated files into this existing component.
        eligible = [path for path in component_patch.added_files if path in unallocated_set]
        new_groups = _file_methods_for_paths(patched, eligible, allocated_within_patch)
        if new_groups:
            seen_paths = {group.file_path for group in component.file_methods}
            for group in new_groups:
                if group.file_path not in seen_paths:
                    component.file_methods.append(group)
                    seen_paths.add(group.file_path)
            component.file_methods.sort(key=lambda group: group.file_path)

    # Mint new components in the scope's namespace. Server-assigned ids; the
    # LLM may also list `owned_files` (subset of `unallocated_files`) which the
    # server materializes into `file_methods` from the analysis files index.
    # Skip a new spec whose `name` already exists in the scope (likely the LLM
    # dressed up an existing component as "new").
    existing_names = {c.name for c in patched.components}
    existing_ids = {c.component_id for c in patched.components}
    for spec in scope_patch.new_components:
        if spec.name in existing_names:
            logger.warning(
                "Skipping new component spec with name '%s': already present in scope.",
                spec.name,
            )
            continue
        new_id = _assign_new_component_id(patch_scope.scope_id, existing_ids)
        existing_ids.add(new_id)
        existing_names.add(spec.name)
        eligible = [path for path in spec.owned_files if path in unallocated_set]
        new_component = Component(
            name=spec.name,
            description=spec.description,
            key_entities=[reference.model_copy(deep=True) for reference in spec.key_entities],
            file_methods=_file_methods_for_paths(patched, eligible, allocated_within_patch),
            component_id=new_id,
        )
        patched.components.append(new_component)
        components_by_id[new_id] = new_component
        target_component_ids.add(new_id)

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
            continue
        relation_patch = returned_relations.pop(_relation_key(relation), None)
        if relation_patch is None:
            relation_patch = _pop_relation_patch_by_names(returned_relations, relation, existing_relation_name_counts)
        if relation_patch is None:
            untouched_relations.append(relation)
            continue
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
            scope_patch = AnalysisScopePatch.model_validate(_salvage_stringified_objects(raw_response))
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
