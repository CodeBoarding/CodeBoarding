"""Plan scoped analysis updates from structural cluster diffs."""

from dataclasses import dataclass, field
from collections.abc import Iterable
import logging
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    MetaAnalysisInsights,
    SourceCodeReference,
    ScopeOperationAction,
    ScopeUpdateDecision,
    ScopedClusterRef,
)
from agents.cluster_ids import CodeBoardingClusterIds
from agents.prompts import get_planning_message, get_system_message
from agents.scope_ids import ROOT_SCOPE_ID
from agents.validation import ValidationResult
from diagram_analysis.cluster_delta import (
    ClusterMemberDelta,
    ClusterRef,
    ClusterReshape,
    LanguageStructuralDiff,
    StructuralClusterDiff,
)
from diagram_analysis.exceptions import InvalidIncrementalPlanError, IncrementalScopeRegenerationRequiredError
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from telemetry.service import telemetry


logger = logging.getLogger(__name__)


_ARCHITECTURE_OUTPUT_CONTRACT = """

## Architecture output contract
- This step plans component boundaries only. Do not define component relations; API surfaces and relations are generated later.
- Preserve an existing component's name, description, and key entities unless its architectural responsibility changed.
- For create_component, provide a clear name and description. Add up to 5 key_entities only when their exact qualified names are available; otherwise leave them empty for deterministic selection.
- For update_component, include refreshed name, description, or key_entities only when the component's responsibility changed. An empty key_entities list preserves the current selection.
- For update_component, delete_component, and noop, copy the exact component_id from the existing-components list.
"""


@dataclass
class ScopeOperationValidationContext:
    expected_cluster_refs: set[ClusterRef] = field(default_factory=set)
    existing_component_ids: set[str] = field(default_factory=set)
    known_qnames: set[str] = field(default_factory=set)
    component_ids_by_cluster_ref: dict[ClusterRef, str] = field(default_factory=dict)
    component_ids_by_name: dict[str, str] = field(default_factory=dict)
    scope_id: str = ROOT_SCOPE_ID


class IncrementalPlanningAgent(CodeBoardingAgent):
    """Decides diagram operations for one structural-diff scope."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights | None,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        changes: ChangeSet | None = None,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message(), agent_llm, parsing_llm)
        if changes is not None:
            self.toolkit.context.changes = changes
        self.agent = create_agent(
            model=agent_llm,
            tools=[self.toolkit.read_source_reference, self.toolkit.list_git_changes],
        )
        self.project_name = project_name
        self.meta_context = meta_context
        self.prompt = PromptTemplate(
            template=get_planning_message(),
            input_variables=[
                "project_name",
                "scope_id",
                "project_type",
                "meta_context",
                "existing_components",
                "changed_files",
                "structural_diff",
            ],
        )

    def decide_scope_update(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        structural_diff: StructuralClusterDiff,
    ) -> ScopeUpdateDecision:
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"
        prompt = self.prompt.format(
            project_name=self.project_name,
            scope_id=scope_id,
            project_type=project_type,
            meta_context=meta_context_str,
            existing_components=_format_scope_components(scope.components),
            changed_files=_format_changed_files(self.toolkit.context.changes),
            structural_diff=format_structural_diff(structural_diff),
        )
        prompt += _ARCHITECTURE_OUTPUT_CONTRACT
        context = ScopeOperationValidationContext(
            expected_cluster_refs=_actionable_new_cluster_refs(structural_diff),
            existing_component_ids={component.component_id for component in scope.components if component.component_id},
            known_qnames=_known_qnames(self.static_analysis),
            component_ids_by_cluster_ref=_component_ids_by_cluster_ref(scope_id, scope.components, structural_diff),
            component_ids_by_name=_component_ids_by_name(scope.components),
            scope_id=scope_id,
        )
        decision = self._validation_invoke(
            prompt,
            ScopeUpdateDecision,
            validators=[validate_scope_update_decision],
            context=context,
            max_validation_attempts=3,
        )
        validation = validate_scope_update_decision(decision, context)
        if not validation.is_valid:
            logger.error(
                "Incremental planning decision remained invalid after retries: %s",
                validation.feedback_messages,
            )
            _track_invalid_planning_decision(scope_id, validation.feedback_messages)
            raise InvalidIncrementalPlanError(scope_id, validation.feedback_messages)
        for operation in decision.operations:
            if operation.action == ScopeOperationAction.REGENERATE_SCOPE:
                raise IncrementalScopeRegenerationRequiredError(scope_id, operation.rationale)
        return decision


def _track_invalid_planning_decision(scope_id: str, feedback_messages: list[str]) -> None:
    telemetry.capture_exception(
        RuntimeError("Incremental planning decision remained invalid after retries"),
        properties={
            "error_type": "incremental_planning_invalid_decision",
            "scope_id": scope_id,
            "issue_count": len(feedback_messages),
            "issues": feedback_messages[:10],
        },
    )
    telemetry.flush()


def validate_scope_update_decision(
    decision: ScopeUpdateDecision,
    context: ScopeOperationValidationContext,
) -> ValidationResult:
    _normalize_scope_update_decision(decision, context)
    errors: list[str] = []
    seen_refs: list[ClusterRef] = []
    for operation in decision.operations:
        refs = [_cluster_ref_from_scoped_ref(ref) for ref in operation.cluster_refs]
        seen_refs.extend(refs)
        if operation.action in {
            ScopeOperationAction.UPDATE_COMPONENT,
            ScopeOperationAction.DELETE_COMPONENT,
            ScopeOperationAction.NOOP,
        }:
            if operation.component_id not in context.existing_component_ids:
                errors.append(
                    f"Operation {operation.action} references unknown component_id={operation.component_id!r}."
                )
        if operation.action == ScopeOperationAction.CREATE_COMPONENT:
            if not operation.name or not operation.description:
                errors.append("create_component operations must include name and description.")
        if operation.action == ScopeOperationAction.NOOP and (
            operation.name or operation.description or operation.key_entities
        ):
            errors.append("noop operations must preserve the component name, description, and key entities.")
        if len(operation.key_entities) > 5:
            errors.append(f"Operation {operation.action} includes more than five key entities.")
        entity_qnames = [entity.qualified_name for entity in operation.key_entities]
        duplicate_qnames = {qname for qname in entity_qnames if entity_qnames.count(qname) > 1}
        if duplicate_qnames:
            errors.append(f"Operation {operation.action} repeats key entities: {sorted(duplicate_qnames)}.")
        unknown_qnames = set(entity_qnames) - context.known_qnames
        if context.known_qnames and unknown_qnames:
            errors.append(f"Operation {operation.action} references unknown key entities: {sorted(unknown_qnames)}.")

    seen_set = set(seen_refs)
    missing = context.expected_cluster_refs - seen_set
    extra = seen_set - context.expected_cluster_refs
    duplicates = {ref for ref in seen_set if seen_refs.count(ref) > 1}
    if missing:
        errors.append(f"Missing cluster_refs: {_format_cluster_ref_list(missing)}")
    if extra:
        errors.append(f"Unexpected cluster_refs: {_format_cluster_ref_list(extra)}")
    if duplicates:
        errors.append(f"Duplicate cluster_refs: {_format_cluster_ref_list(duplicates)}")
    return ValidationResult(is_valid=not errors, feedback_messages=errors)


def _normalize_scope_update_decision(
    decision: ScopeUpdateDecision,
    context: ScopeOperationValidationContext,
) -> None:
    """Repair unambiguous routing and optional key-entity metadata."""
    qname_aliases = _unique_qname_aliases(context.known_qnames)
    routed_operations = 0
    canonicalized_qnames = 0
    dropped_qnames: set[str] = set()
    for operation in decision.operations:
        refs = {_cluster_ref_from_scoped_ref(ref) for ref in operation.cluster_refs}
        if operation.component_id is None and operation.action in {
            ScopeOperationAction.UPDATE_COMPONENT,
            ScopeOperationAction.DELETE_COMPONENT,
            ScopeOperationAction.NOOP,
        }:
            owner_ids = {
                context.component_ids_by_cluster_ref[ref] for ref in refs if ref in context.component_ids_by_cluster_ref
            }
            if len(owner_ids) == 1:
                operation.component_id = next(iter(owner_ids))
            elif not owner_ids and operation.name:
                operation.component_id = context.component_ids_by_name.get(_normalize_component_name(operation.name))
            if operation.component_id is not None:
                routed_operations += 1

        if operation.action not in {ScopeOperationAction.CREATE_COMPONENT, ScopeOperationAction.UPDATE_COMPONENT}:
            continue

        normalized_entities: list[SourceCodeReference] = []
        seen_qnames: set[str] = set()
        for entity in operation.key_entities:
            qname = entity.qualified_name
            canonical_qname = qname if qname in context.known_qnames else qname_aliases.get(_qname_alias(qname))
            if context.known_qnames and canonical_qname is None:
                dropped_qnames.add(qname)
                continue
            canonical_qname = canonical_qname or qname
            if canonical_qname in seen_qnames:
                continue
            if canonical_qname != qname:
                canonicalized_qnames += 1
            entity.qualified_name = canonical_qname
            normalized_entities.append(entity)
            seen_qnames.add(canonical_qname)
            if len(normalized_entities) == 5:
                break
        operation.key_entities = normalized_entities

    if routed_operations or canonicalized_qnames:
        logger.info(
            "Normalized incremental plan: routed %d operation(s), canonicalized %d key-entity qname(s)",
            routed_operations,
            canonicalized_qnames,
        )
    if dropped_qnames:
        logger.warning("Dropped unresolved optional key entities: %s", sorted(dropped_qnames))


def _component_ids_by_cluster_ref(
    scope_id: str,
    components: list[Component],
    structural_diff: StructuralClusterDiff,
) -> dict[ClusterRef, str]:
    """Map new-side cluster refs to a unique existing owner when overlap proves one."""
    prefix = "" if scope_id == ROOT_SCOPE_ID else f"{scope_id}."
    owners_by_cluster_id: dict[str, set[str]] = {}
    for component in components:
        if not component.component_id:
            continue
        for cluster_id in component.source_cluster_ids:
            owners_by_cluster_id.setdefault(cluster_id, set()).add(component.component_id)

    def owner_for_old_ref(ref: ClusterRef) -> str | None:
        owners = owners_by_cluster_id.get(f"{prefix}{ref.cluster_id}", set())
        return next(iter(owners)) if len(owners) == 1 else None

    owners_by_new_ref: dict[ClusterRef, set[str]] = {}
    for language_diff in structural_diff.by_language.values():
        for delta in language_diff.modified:
            owner = owner_for_old_ref(delta.old_cluster)
            if owner:
                owners_by_new_ref.setdefault(delta.new_cluster, set()).add(owner)
        for reshape in language_diff.reshaped:
            for (old_ref, new_ref), overlap in reshape.overlap_counts.items():
                if overlap <= 0:
                    continue
                owner = owner_for_old_ref(old_ref)
                if owner:
                    owners_by_new_ref.setdefault(new_ref, set()).add(owner)

    return {ref: next(iter(owner_ids)) for ref, owner_ids in owners_by_new_ref.items() if len(owner_ids) == 1}


def _component_ids_by_name(components: list[Component]) -> dict[str, str]:
    ids_by_name: dict[str, set[str]] = {}
    for component in components:
        if component.component_id:
            ids_by_name.setdefault(_normalize_component_name(component.name), set()).add(component.component_id)
    return {name: next(iter(component_ids)) for name, component_ids in ids_by_name.items() if len(component_ids) == 1}


def _normalize_component_name(name: str) -> str:
    return " ".join(name.casefold().split())


def _qname_alias(qname: str) -> str:
    return qname.replace("-", "_")


def _unique_qname_aliases(known_qnames: set[str]) -> dict[str, str]:
    qnames_by_alias: dict[str, set[str]] = {}
    for qname in known_qnames:
        qnames_by_alias.setdefault(_qname_alias(qname), set()).add(qname)
    return {alias: next(iter(qnames)) for alias, qnames in qnames_by_alias.items() if len(qnames) == 1}


def _cluster_ref_from_scoped_ref(ref: ScopedClusterRef) -> ClusterRef:
    return ClusterRef(ref.language, ref.cluster_id, ref.scope_id)


def format_structural_diff(structural_diff: StructuralClusterDiff) -> str:
    sections: list[str] = []
    for language in sorted(structural_diff.by_language):
        sections.append(_format_language_diff(structural_diff.by_language[language]))
    return "\n".join(section for section in sections if section) or "No structural changes."


def _format_language_diff(diff: LanguageStructuralDiff) -> str:
    lines = [f"## {diff.language}"]
    if diff.modified:
        lines.append("### Modified clusters")
        lines.extend(_format_member_delta(delta) for delta in diff.modified)
    if diff.new:
        lines.append("### New clusters")
        if diff.new_details:
            lines.extend(_format_new_cluster(delta) for delta in diff.new_details)
        else:
            lines.extend(f"- {_format_cluster_ref(ref)}" for ref in _sort_cluster_refs(diff.new))
    if diff.removed:
        lines.append("### Removed clusters")
        lines.extend(f"- {_format_cluster_ref(ref)}" for ref in _sort_cluster_refs(diff.removed))
    if diff.reshaped:
        lines.append("### Reshaped clusters")
        lines.extend(_format_reshape(reshape) for reshape in diff.reshaped)
    if len(lines) == 1:
        lines.append("No structural changes.")
    return "\n".join(lines)


def _format_member_delta(delta: ClusterMemberDelta) -> str:
    parts = [f"- {_format_cluster_ref(delta.old_cluster)} -> {_format_cluster_ref(delta.new_cluster)}"]
    if delta.added_methods:
        parts.append(f"added={sorted(delta.added_methods)}")
    if delta.removed_methods:
        parts.append(f"removed={sorted(delta.removed_methods)}")
    if delta.dirty_files:
        parts.append(f"dirty_files={sorted(delta.dirty_files)}")
    return "; ".join(parts)


def _format_new_cluster(delta: ClusterMemberDelta) -> str:
    parts = [f"- {_format_cluster_ref(delta.new_cluster)}"]
    if delta.added_methods:
        parts.append(f"methods={sorted(delta.added_methods)}")
    if delta.dirty_files:
        parts.append(f"files={sorted(delta.dirty_files)}")
    return "; ".join(parts)


def _format_reshape(reshape: ClusterReshape) -> str:
    old_refs = ", ".join(_format_cluster_ref(ref) for ref in _sort_cluster_refs(reshape.old_clusters))
    new_refs = ", ".join(_format_cluster_ref(ref) for ref in _sort_cluster_refs(reshape.new_clusters))
    overlaps = ", ".join(
        f"{_format_cluster_ref(old_ref)}->{_format_cluster_ref(new_ref)}:{count}"
        for (old_ref, new_ref), count in sorted(
            reshape.overlap_counts.items(),
            key=lambda item: (
                item[0][0].scope_id,
                item[0][0].language,
                item[0][0].cluster_id,
                item[0][1].scope_id,
                item[0][1].language,
                item[0][1].cluster_id,
            ),
        )
    )
    suffix = f"; dirty_files={sorted(reshape.dirty_files)}" if reshape.dirty_files else ""
    return f"- old=[{old_refs}] new=[{new_refs}] overlaps=[{overlaps}]{suffix}"


def _actionable_new_cluster_refs(structural_diff: StructuralClusterDiff) -> set[ClusterRef]:
    refs: set[ClusterRef] = set()
    for diff in structural_diff.by_language.values():
        refs.update(delta.new_cluster for delta in diff.modified)
        refs.update(diff.new)
        for reshape in diff.reshaped:
            refs.update(reshape.new_clusters)
    return refs


def _format_scope_components(components: list[Component]) -> str:
    if not components:
        return "No existing components in this scope."
    lines: list[str] = []
    for component in components:
        key_entities = ", ".join(entity.qualified_name for entity in component.key_entities) or "None"
        lines.append(
            f'- {component.component_id or "?"} "{component.name}" '
            f"clusters=[{_format_component_cluster_ids(component.source_cluster_ids)}] -- "
            f"{(component.description or '').strip()} -- key_entities=[{key_entities}]"
        )
    return "\n".join(lines)


def _format_component_cluster_ids(cluster_ids: list[str]) -> str:
    return ", ".join(CodeBoardingClusterIds.sort(set(cluster_ids))) or "None"


def _format_changed_files(changes: ChangeSet | None) -> str:
    if changes is None or not changes.files:
        return "No changed-file summary available."
    lines = []
    for file_change in changes.files:
        if file_change.old_path:
            lines.append(f"- {file_change.status_code} {file_change.old_path} -> {file_change.file_path}")
        else:
            lines.append(f"- {file_change.status_code} {file_change.file_path}")
    return "\n".join(lines)


def _known_qnames(static_analysis: StaticAnalysisResults) -> set[str]:
    qnames: set[str] = set()
    for language in static_analysis.get_languages():
        try:
            qnames.update(static_analysis.get_cfg(language).nodes)
        except (KeyError, ValueError):
            continue
    return qnames


def _format_cluster_ref(ref: ClusterRef) -> str:
    return f"{ref.scope_id}:{ref.language}:{ref.cluster_id}"


def _sort_cluster_refs(refs: Iterable[ClusterRef]) -> list[ClusterRef]:
    return sorted(refs, key=lambda ref: (ref.scope_id, ref.language, ref.cluster_id))


def _format_cluster_ref_list(refs: set[ClusterRef]) -> str:
    if not refs:
        return "None"
    return ", ".join(_format_cluster_ref(ref) for ref in _sort_cluster_refs(refs))
